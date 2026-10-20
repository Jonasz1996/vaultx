"""Persoonlijke kluis: activeren vanuit VaultX en items/mappen voor Bitwarden-clients.

Activeren gebeurt in de VaultX-webinterface, waar de gebruiker al via Authentik
is ingelogd. De browser leidt de sleutels af uit het master password en stuurt
alleen versleutelde sleutels en een afgeleide hash. Zo is een kluis altijd aan
een Authentik-identiteit gekoppeld, en kan niemand een kluis aanmaken voor het
e-mailadres van iemand anders.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, PermissionDeniedError
from app.core.security import (
    hash_master_password,
    hash_token,
    open_secret,
    seal_secret,
    verify_master_password,
)
from app.models import UserSession, VaultAccount, VaultCipher, VaultDevice, VaultFolder, WebAuthnCredential
from app.models.base import new_uuid
from app.repositories import (
    VaultAccountRepository,
    VaultCipherRepository,
    VaultDeviceRepository,
    VaultFolderRepository,
    VaultNoteRepository,
    WebAuthnCredentialRepository,
)
from app.schemas.vault import VaultEnroll, VaultPasswordChange
from app.services import totp
from app.services.audit import AuditService
from app.services.principal import Principal
from app.services.vault_auth import (
    VaultCaller,
    clear_two_factor,
    clear_vault_two_factor,
    device_type_name,
    normalize_email,
    totp_context,
)
from app.services.vault_push import PushType, publish
from app.services.webauthn import SCOPE_VAULT, WebAuthnError, WebAuthnService

# Kluis verwijderen zonder master password (vergeten) mag enkel kort na een nieuwe login.
RESET_FRESH_LOGIN_MINUTES = 10


class StaleCipherError(Exception):
    """De client werkt met een verouderde versie van het item."""


def new_security_stamp() -> str:
    return secrets.token_hex(16)


@dataclass(slots=True)
class VaultStatus:
    email: str | None
    blocked_reason: str | None
    account: VaultAccount | None
    devices: list[VaultDevice]
    item_count: int = 0
    trash_count: int = 0
    folder_count: int = 0
    note_count: int = 0


class VaultService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.accounts = VaultAccountRepository(db)
        self.devices = VaultDeviceRepository(db)
        self.folders = VaultFolderRepository(db)
        self.ciphers = VaultCipherRepository(db)
        self.notes = VaultNoteRepository(db)
        self.audit = AuditService(db)

    # --- Beheer vanuit VaultX (ingelogd via Authentik) --------------------------------

    @staticmethod
    def _enroll_email(p: Principal) -> tuple[str | None, str | None]:
        if not p.user.email:
            return None, "Je account in Authentik heeft geen e-mailadres. Vul het daar in en log opnieuw in."
        return normalize_email(p.user.email), None

    async def status(self, p: Principal) -> VaultStatus:
        account = await self.accounts.get(p.user.id)
        if account is None:
            email, blocked = self._enroll_email(p)
            if email and await self.accounts.by_email(email) is not None:
                blocked = "Dit e-mailadres is al in gebruik voor een andere kluis."
            return VaultStatus(email=email, blocked_reason=blocked, account=None, devices=[])
        items, trash = await self.ciphers.counts(account.user_id)
        return VaultStatus(
            email=account.email,
            blocked_reason=None,
            account=account,
            devices=await self.devices.for_user(account.user_id),
            item_count=items,
            trash_count=trash,
            folder_count=len(await self.folders.for_user(account.user_id)),
            note_count=(await self.notes.counts(account.user_id))[0],
        )

    async def enroll(self, p: Principal, data: VaultEnroll) -> VaultAccount:
        if await self.accounts.get(p.user.id) is not None:
            raise ConflictError("Je kluis is al geactiveerd")
        email, blocked = self._enroll_email(p)
        if email is None:
            raise InvalidOperationError(blocked)
        if data.salt != email:
            # De browser moet de sleutels afleiden met precies de salt die clients later krijgen.
            raise InvalidOperationError("Salt komt niet overeen met het e-mailadres van je kluis")
        if await self.accounts.by_email(email) is not None:
            raise ConflictError("Dit e-mailadres is al in gebruik voor een andere kluis")
        now = datetime.now(UTC)
        account = self.accounts.add(
            VaultAccount(
                user_id=p.user.id,
                email=email,
                kdf_salt=email,
                kdf_type=data.kdf,
                kdf_iterations=data.kdf_iterations,
                kdf_memory=data.kdf_memory,
                kdf_parallelism=data.kdf_parallelism,
                master_password_hash=await run_in_threadpool(hash_master_password, data.master_password_hash),
                user_key=data.user_key,
                public_key=data.public_key,
                private_key=data.private_key,
                security_stamp=new_security_stamp(),
                revision_date=now,
                failed_logins=0,
                created_at=now,
                updated_at=now,
            )
        )
        await self.db.flush()
        await self.audit.record(
            "vault.enroll",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"email": email, "kdf": data.kdf, "kdf_iterations": data.kdf_iterations},
        )
        await self.db.commit()
        return account

    async def _fresh_session(self, p: Principal) -> bool:
        if p.auth_method != "session" or p.session_id is None:
            return False
        session = await self.db.get(UserSession, p.session_id)
        if session is None:
            return False
        return session.created_at > datetime.now(UTC) - timedelta(minutes=RESET_FRESH_LOGIN_MINUTES)

    @staticmethod
    def _count_failure(account: VaultAccount, settings: Settings) -> bool:
        """Eén mislukte poging erbij; True als het account nu vergrendeld is."""
        account.failed_logins += 1
        if account.failed_logins < settings.vault_max_failed_logins:
            return False
        account.locked_until = datetime.now(UTC) + timedelta(minutes=settings.vault_lockout_minutes)
        account.failed_logins = 0
        return True

    async def _verify_master(
        self,
        p: Principal,
        master_password_hash: str,
        settings: Settings,
        action: str,
        *,
        second_step: bool = False,
    ) -> VaultAccount:
        """Controleert het master password in de webinterface, met dezelfde grens op mislukte
        pogingen als bij inloggen vanuit een app: het telt samen. Commit bij een fout."""
        account = await self.accounts.lock(p.user.id)
        if account is None:
            raise NotFoundError("Je hebt nog geen kluis")
        now = datetime.now(UTC)
        if account.locked_until is not None and account.locked_until > now:
            await self.audit.record(
                action,
                p.actor,
                outcome="denied",
                target_type="vault",
                target_id=p.user.id,
                details={"reason": "tijdelijk vergrendeld"},
            )
            await self.db.commit()
            raise PermissionDeniedError("Te veel mislukte pogingen. Probeer het over enkele minuten opnieuw.")
        valid = await run_in_threadpool(
            verify_master_password, master_password_hash, account.master_password_hash
        )
        if not valid:
            details: dict[str, Any] = {"reason": "fout master password"}
            if self._count_failure(account, settings):
                details["locked_until"] = account.locked_until.isoformat()
            await self.audit.record(
                action,
                p.actor,
                outcome="failure",
                target_type="vault",
                target_id=p.user.id,
                details=details,
            )
            await self.db.commit()
            raise InvalidOperationError("Master password is onjuist")
        if not second_step:
            # Met een tweede stap erna (2FA-code) telt pas het geheel als geslaagd.
            account.failed_logins = 0
            account.locked_until = None
        return account

    async def unlock(self, p: Principal, master_password_hash: str, settings: Settings) -> VaultAccount:
        """Ontgrendelen in de webinterface (notities, fase 7).

        De browser bewijst het master password met dezelfde afgeleide hash als een
        Bitwarden-client en krijgt dan de versleutelde user key.
        """
        account = await self._verify_master(p, master_password_hash, settings, "vault.unlock")
        await self.audit.record("vault.unlock", p.actor, target_type="vault", target_id=p.user.id)
        await self.db.commit()
        return account

    async def change_password(
        self, p: Principal, data: VaultPasswordChange, settings: Settings
    ) -> VaultAccount:
        """Nieuw master password en/of nieuwe KDF. Zoals bij Bitwarden worden daarna alle
        apparaten afgemeld: hun tokens en hun afgeleide sleutels kloppen niet meer."""
        account = await self._verify_master(
            p, data.current_master_password_hash, settings, "vault.password.change"
        )
        now = datetime.now(UTC)
        account.kdf_type = data.kdf
        account.kdf_iterations = data.kdf_iterations
        account.kdf_memory = data.kdf_memory
        account.kdf_parallelism = data.kdf_parallelism
        account.master_password_hash = await run_in_threadpool(
            hash_master_password, data.master_password_hash
        )
        account.user_key = data.user_key
        account.security_stamp = new_security_stamp()
        account.revision_date = max(now, account.revision_date + timedelta(microseconds=1))
        account.updated_at = now
        revoked = 0
        for device in await self.devices.for_user(account.user_id):
            device.remember_token_hash = None
            device.remember_until = None
            if device.revoked_at is None:
                device.revoked_at = now
                revoked += 1
        await self.audit.record(
            "vault.password.change",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"kdf": data.kdf, "kdf_iterations": data.kdf_iterations, "devices_signed_out": revoked},
        )
        await publish(self.db, account.user_id, PushType.LOG_OUT)
        await self.db.commit()
        return account

    # --- Tweestapsverificatie voor de Bitwarden-apps (fase 9) -------------------------
    # Twee soorten tweede stap, naast elkaar: een authenticator-app (TOTP) en
    # beveiligingssleutels/passkeys (WebAuthn). two_factor_enabled_at staat zolang er minstens
    # één is; de herstelcode hoort bij het geheel en wordt gemaakt als de eerste erbij komt.

    def _seal_totp(self, settings: Settings, account: VaultAccount, secret: str) -> bytes:
        return seal_secret(settings.secret_key.get_secret_value(), secret, totp_context(account.user_id))

    def _open_totp(self, settings: Settings, account: VaultAccount, sealed: bytes) -> str:
        return open_secret(settings.secret_key.get_secret_value(), sealed, totp_context(account.user_id))

    async def security_keys(self, user_id: UUID, settings: Settings) -> list[WebAuthnCredential]:
        return await WebAuthnService(self.db, settings).credentials(user_id, SCOPE_VAULT)

    async def _turn_on(self, account: VaultAccount) -> str | None:
        """Eerste tweede stap erbij: herstelcode maken. Geeft die terug (eenmalig te tonen),
        of None als tweestapsverificatie al aan stond."""
        if account.two_factor_enabled:
            return None
        recovery = totp.new_recovery_code()
        account.recovery_code_hash = hash_token(totp.normalize_recovery_code(recovery))
        account.two_factor_enabled_at = datetime.now(UTC)
        # Oude "onthoud mij"-tokens uit een vorige periode gelden niet meer.
        for device in await self.devices.for_user(account.user_id):
            device.remember_token_hash = None
            device.remember_until = None
        return recovery

    async def _turn_off_if_empty(self, account: VaultAccount, settings: Settings) -> bool:
        if account.totp_secret is not None or await self.security_keys(account.user_id, settings):
            return False
        clear_two_factor(account)
        return True

    async def _verify_second_factor(
        self,
        p: Principal,
        account: VaultAccount,
        settings: Settings,
        action: str,
        code: str | None,
        security_key: str | None,
    ) -> str:
        """Bewijs dat je de tweede stap hebt (code uit de app, herstelcode of een gekoppelde
        sleutel) voor je er iets aan wijzigt. Een fout telt mee voor de vergrendeling: anders
        raadt wie je sessie en master password heeft de zes cijfers gewoon tot hij er is."""
        via = None
        if security_key:
            try:
                cred = await WebAuthnService(self.db, settings).verify(
                    account.user_id, SCOPE_VAULT, security_key
                )
                via = f"beveiligingssleutel ({cred.name})"
            except WebAuthnError:
                via = None
        elif code:
            if account.totp_secret is not None:
                secret = self._open_totp(settings, account, account.totp_secret)
                step = totp.verify(secret, code, account.totp_last_step)
                if step is not None:
                    account.totp_last_step = step
                    via = "authenticator"
            if (
                via is None
                and account.recovery_code_hash
                and hmac.compare_digest(
                    hash_token(totp.normalize_recovery_code(code)), account.recovery_code_hash
                )
            ):
                via = "herstelcode"
        if via is None:
            self._count_failure(account, settings)
            await self.audit.record(
                action,
                p.actor,
                outcome="failure",
                target_type="vault",
                target_id=p.user.id,
                details={"reason": "tweede stap ontbreekt of klopt niet"},
            )
            await self.db.commit()
            raise InvalidOperationError(
                "De code klopt niet"
                if code or not security_key
                else "De beveiligingssleutel werd niet aanvaard"
            )
        account.failed_logins = 0
        return via

    async def setup_two_factor(
        self, p: Principal, master_password_hash: str, settings: Settings
    ) -> tuple[str, str]:
        account = await self._verify_master(p, master_password_hash, settings, "vault.2fa.setup")
        if account.totp_secret is not None:
            raise ConflictError("Er is al een authenticator-app gekoppeld. Haal die eerst weg.")
        secret = totp.new_secret()
        account.totp_pending = self._seal_totp(settings, account, secret)
        await self.db.commit()
        return secret, totp.otpauth_uri(secret, account.email)

    async def enable_two_factor(self, p: Principal, code: str, settings: Settings) -> str | None:
        account = await self.accounts.lock(p.user.id)
        if account is None:
            raise NotFoundError("Je hebt nog geen kluis")
        if account.totp_secret is not None:
            raise ConflictError("Er is al een authenticator-app gekoppeld")
        if account.totp_pending is None:
            raise InvalidOperationError("Begin opnieuw: er is geen nieuwe koppeling klaar")
        secret = self._open_totp(settings, account, account.totp_pending)
        step = totp.verify(secret, code, None)
        if step is None:
            raise InvalidOperationError(
                "De code klopt niet. Controleer de klok van je telefoon en probeer opnieuw."
            )
        account.totp_secret = account.totp_pending
        account.totp_pending = None
        account.totp_last_step = step
        recovery = await self._turn_on(account)
        await self.audit.record(
            "vault.2fa.enable",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"method": "authenticator"},
        )
        await self.db.commit()
        return recovery

    async def remove_authenticator(
        self,
        p: Principal,
        master_password_hash: str,
        code: str | None,
        security_key: str | None,
        settings: Settings,
    ) -> None:
        """Enkel de authenticator-app weghalen; gekoppelde sleutels blijven."""
        action = "vault.2fa.disable"
        account = await self._verify_master(p, master_password_hash, settings, action, second_step=True)
        if account.totp_secret is None:
            raise InvalidOperationError("Er is geen authenticator-app gekoppeld")
        via = await self._verify_second_factor(p, account, settings, action, code, security_key)
        account.totp_secret = None
        account.totp_pending = None
        account.totp_last_step = None
        off = await self._turn_off_if_empty(account, settings)
        await self.audit.record(
            action,
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"method": "authenticator", "via": via, "two_factor_off": off},
        )
        await self.db.commit()

    async def disable_two_factor(
        self,
        p: Principal,
        master_password_hash: str,
        code: str | None,
        settings: Settings,
        security_key: str | None = None,
    ) -> None:
        account = await self._verify_master(
            p, master_password_hash, settings, "vault.2fa.disable", second_step=True
        )
        if not account.two_factor_enabled:
            raise InvalidOperationError("Tweestapsverificatie staat niet aan")
        via = await self._verify_second_factor(p, account, settings, "vault.2fa.disable", code, security_key)
        await clear_vault_two_factor(self.db, settings, account)
        await self.audit.record(
            "vault.2fa.disable", p.actor, target_type="vault", target_id=p.user.id, details={"via": via}
        )
        await self.db.commit()

    async def two_factor_challenge(self, p: Principal, settings: Settings) -> dict | None:
        """Opties om met een gekoppelde sleutel te bewijzen dat je hem hebt (bij weghalen)."""
        svc = WebAuthnService(self.db, settings)
        return svc.assertion_options(p.user.id, SCOPE_VAULT, await svc.credentials(p.user.id, SCOPE_VAULT))

    async def security_key_options(self, p: Principal, master_password_hash: str, settings: Settings) -> dict:
        account = await self._verify_master(p, master_password_hash, settings, "vault.2fa.setup")
        await self.db.commit()
        try:
            return await WebAuthnService(self.db, settings).registration_options(
                p.user.id, SCOPE_VAULT, account.email, p.user.display_name or account.email
            )
        except WebAuthnError as exc:
            raise InvalidOperationError(str(exc)) from exc

    async def add_security_key(
        self, p: Principal, name: str, credential: str, settings: Settings
    ) -> tuple[WebAuthnCredential, str | None]:
        """Tweede helft van het koppelen. De challenge kreeg je pas na je master password
        (security_key_options), dus die hoeft hier niet opnieuw."""
        account = await self.accounts.lock(p.user.id)
        if account is None:
            raise NotFoundError("Je hebt nog geen kluis")
        try:
            cred = await WebAuthnService(self.db, settings).register(p.user.id, SCOPE_VAULT, name, credential)
        except WebAuthnError as exc:
            raise InvalidOperationError(str(exc)) from exc
        recovery = await self._turn_on(account)
        await self.audit.record(
            "vault.2fa.enable",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"method": "beveiligingssleutel", "name": cred.name, "passkey": cred.backed_up},
        )
        await self.db.commit()
        return cred, recovery

    async def remove_security_key(
        self,
        p: Principal,
        key_id: UUID,
        master_password_hash: str,
        code: str | None,
        security_key: str | None,
        settings: Settings,
    ) -> None:
        action = "vault.2fa.disable"
        account = await self._verify_master(p, master_password_hash, settings, action, second_step=True)
        cred = await self.db.get(WebAuthnCredential, key_id)
        if cred is None or cred.user_id != p.user.id or cred.scope != SCOPE_VAULT:
            raise NotFoundError("Sleutel niet gevonden")
        via = await self._verify_second_factor(p, account, settings, action, code, security_key)
        name = cred.name
        await self.db.delete(cred)
        await self.db.flush()
        off = await self._turn_off_if_empty(account, settings)
        await self.audit.record(
            action,
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"method": "beveiligingssleutel", "name": name, "via": via, "two_factor_off": off},
        )
        await self.db.commit()

    async def admin_reset_two_factor(self, p: Principal, user_id: UUID, settings: Settings) -> None:
        """Beheerder: telefoon, sleutels en herstelcode kwijt. Het master password blijft nodig."""
        if not p.is_admin:
            raise await self.audit.deny("vault.2fa.disable", p.actor, target_type="vault", target_id=user_id)
        account = await self.accounts.lock(user_id)
        if account is None or not account.two_factor_enabled:
            raise NotFoundError("Deze gebruiker heeft geen tweestapsverificatie op de kluis")
        await clear_vault_two_factor(self.db, settings, account)
        await self.audit.record(
            "vault.2fa.disable",
            p.actor,
            target_type="vault",
            target_id=user_id,
            details={"via": "beheerder"},
        )
        await self.db.commit()

    async def reset(self, p: Principal, master_password_hash: str | None, settings: Settings) -> None:
        """Verwijdert de kluis met alle items. Zonder master password is de inhoud
        toch onleesbaar; dit is de enige 'herstel'-optie bij een vergeten wachtwoord.

        Bewijs vereist: het master password, of (als je dat kwijt bent) een browsersessie
        die net is ingelogd. Een gestolen of vergeten open sessie wist zo niet je kluis."""
        if master_password_hash:
            account = await self._verify_master(p, master_password_hash, settings, "vault.reset")
        else:
            if not await self._fresh_session(p):
                raise await self.audit.deny(
                    "vault.reset",
                    p.actor,
                    target_type="vault",
                    target_id=p.user.id,
                    message=(
                        "Vul je master password in. Ben je het vergeten, log dan opnieuw in en "
                        f"verwijder de kluis binnen {RESET_FRESH_LOGIN_MINUTES} minuten."
                    ),
                )
            account = await self.accounts.lock(p.user.id)
            if account is None:
                raise NotFoundError("Je hebt nog geen kluis")
        items, trash = await self.ciphers.counts(account.user_id)
        await WebAuthnCredentialRepository(self.db).delete_all(account.user_id, SCOPE_VAULT)
        await self.accounts.delete(account)
        await self.audit.record(
            "vault.reset",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"items_deleted": items + trash},
        )
        await publish(self.db, p.user.id, PushType.LOG_OUT)
        await self.db.commit()

    async def revoke_device(self, p: Principal, device_id: UUID) -> VaultDevice:
        device = await self.devices.get(device_id)
        if device is None or device.user_id != p.user.id:
            raise NotFoundError("Apparaat niet gevonden")
        device.remember_token_hash = None
        device.remember_until = None
        if device.revoked_at is None:
            device.revoked_at = datetime.now(UTC)
            await self.audit.record(
                "vault.device.revoke",
                p.actor,
                target_type="vault_device",
                target_id=device.id,
                details={"device": device.name, "client": device_type_name(device.type)},
            )
            await publish(self.db, device.user_id, PushType.LOG_OUT, device_id=device.id)
            await self.db.commit()
        return device

    # --- Bitwarden-clients -------------------------------------------------------------

    async def _touch(self, c: VaultCaller) -> datetime:
        """Lockt het account en zet een nieuwe revisiedatum (strikt stijgend)."""
        account = await self.accounts.lock(c.account.user_id)
        assert account is not None
        now = datetime.now(UTC)
        if account.revision_date >= now:
            now = account.revision_date + timedelta(microseconds=1)
        account.revision_date = now
        c.account = account
        return now

    async def set_user_key_id(self, c: VaultCaller, user_key_id: str) -> None:
        """De client meldt het id van zijn user key (nieuw in de Bitwarden-SDK)."""
        account = await self.accounts.lock(c.account.user_id)
        assert account is not None
        if account.user_key_id != user_key_id:
            account.user_key_id = user_key_id
            await self._record(c, "vault.user_key_id.set", "vault", account.user_id, user_key_id=user_key_id)
            await self.db.commit()

    async def _record(
        self, c: VaultCaller, action: str, target_type: str, target_id: Any, **details: Any
    ) -> None:
        await self.audit.record(
            action,
            c.actor,
            target_type=target_type,
            target_id=target_id,
            details={"client": c.client_label, **details},
        )

    async def push(
        self, c: VaultCaller, type_: PushType, item_id: UUID | None = None, revision: datetime | None = None
    ) -> None:
        """Meldt de wijziging aan de andere apparaten (live sync), bij de commit."""
        await publish(
            self.db, c.account.user_id, type_, item_id=item_id, revision=revision, context=c.device.identifier
        )

    async def _push_many(
        self, c: VaultCaller, ciphers: list[VaultCipher], type_: PushType, revision: datetime
    ) -> None:
        """Eén item: een gerichte melding. Meerdere: zoals Bitwarden één 'sync je items'."""
        if len(ciphers) == 1:
            await self.push(c, type_, ciphers[0].id, revision)
        elif ciphers:
            await self.push(c, PushType.SYNC_CIPHERS, revision=revision)

    async def _folder_id(self, c: VaultCaller, folder_id: UUID | None) -> UUID | None:
        if folder_id is not None and await self.folders.owned(c.account.user_id, folder_id) is None:
            raise InvalidOperationError("Map niet gevonden")
        return folder_id

    async def sync_data(self, c: VaultCaller) -> tuple[list[VaultFolder], list[VaultCipher]]:
        return (
            await self.folders.for_user(c.account.user_id),
            await self.ciphers.for_user(c.account.user_id),
        )

    async def get_cipher(self, c: VaultCaller, cipher_id: UUID) -> VaultCipher:
        cipher = await self.ciphers.owned(c.account.user_id, cipher_id)
        if cipher is None:
            raise NotFoundError("Item niet gevonden")
        return cipher

    async def list_ciphers(self, c: VaultCaller) -> list[VaultCipher]:
        return [x for x in await self.ciphers.for_user(c.account.user_id) if x.deleted_at is None]

    async def create_cipher(
        self,
        c: VaultCaller,
        *,
        type_: int,
        payload: dict[str, Any],
        folder_id: UUID | None,
        favorite: bool,
        archived_at: datetime | None,
    ) -> VaultCipher:
        folder_id = await self._folder_id(c, folder_id)
        now = await self._touch(c)
        cipher = self.ciphers.add(
            VaultCipher(
                user_id=c.account.user_id,
                folder_id=folder_id,
                type=type_,
                data=payload,
                favorite=favorite,
                created_at=now,
                revision_date=now,
                archived_at=archived_at,
            )
        )
        await self.db.flush()
        await self._record(c, "vault.item.create", "vault_item", cipher.id, type=type_)
        await self.push(c, PushType.SYNC_CIPHER_CREATE, cipher.id, now)
        await self.db.commit()
        return cipher

    async def import_items(
        self,
        c: VaultCaller,
        *,
        folders: list[tuple[UUID | None, str]],
        ciphers: list[tuple[int, dict[str, Any], bool, datetime | None]],
        relationships: list[tuple[int, int]],
    ) -> tuple[int, int]:
        """Importeert een export die de client al versleuteld heeft, in één transactie.

        `folders` zijn (bestaand id of None, naam); een id dat niet van deze gebruiker is,
        wordt een nieuwe map (zoals bij Bitwarden). `relationships` koppelt de index van
        een item aan de index van een map. Geeft (nieuwe items, nieuwe mappen) terug.
        """
        folder_of: dict[int, int] = {}
        for item_index, folder_index in relationships:
            if item_index >= len(ciphers) or folder_index >= len(folders):
                raise InvalidOperationError("Ongeldige koppeling tussen item en map in de import")
            folder_of[item_index] = folder_index
        now = await self._touch(c)
        folder_ids: list[UUID] = []
        created_folders = 0
        for existing_id, name in folders:
            folder = await self.folders.owned(c.account.user_id, existing_id) if existing_id else None
            if folder is None:
                folder = self.folders.add(
                    VaultFolder(id=new_uuid(), user_id=c.account.user_id, name=name, revision_date=now)
                )
                created_folders += 1
            folder_ids.append(folder.id)
        await self.db.flush()
        for index, (type_, payload, favorite, archived_at) in enumerate(ciphers):
            self.ciphers.add(
                VaultCipher(
                    user_id=c.account.user_id,
                    folder_id=folder_ids[folder_of[index]] if index in folder_of else None,
                    type=type_,
                    data=payload,
                    favorite=favorite,
                    created_at=now,
                    revision_date=now,
                    archived_at=archived_at,
                )
            )
        await self.db.flush()
        await self._record(
            c, "vault.import", "vault", c.account.user_id, items=len(ciphers), folders=created_folders
        )
        await self.push(c, PushType.SYNC_VAULT, revision=now)
        await self.db.commit()
        return len(ciphers), created_folders

    async def update_cipher(
        self,
        c: VaultCaller,
        cipher_id: UUID,
        *,
        type_: int,
        payload: dict[str, Any],
        folder_id: UUID | None,
        favorite: bool,
        archived_at: datetime | None,
        last_known_revision: datetime | None,
    ) -> VaultCipher:
        cipher = await self.get_cipher(c, cipher_id)
        # Zelfde regel als Bitwarden: een client met een oudere kopie mag niet overschrijven.
        # Eén seconde marge, want clients ronden af op milliseconden.
        if (
            last_known_revision is not None
            and abs((cipher.revision_date - last_known_revision.astimezone(UTC)).total_seconds()) > 1
        ):
            raise StaleCipherError()
        folder_id = await self._folder_id(c, folder_id)
        now = await self._touch(c)
        cipher.type = type_
        cipher.data = payload
        cipher.folder_id = folder_id
        cipher.favorite = favorite
        cipher.archived_at = archived_at
        cipher.revision_date = now
        await self._record(c, "vault.item.update", "vault_item", cipher.id, type=type_)
        await self.push(c, PushType.SYNC_CIPHER_UPDATE, cipher.id, now)
        await self.db.commit()
        return cipher

    async def update_cipher_partial(
        self, c: VaultCaller, cipher_id: UUID, *, folder_id: UUID | None, favorite: bool
    ) -> VaultCipher:
        cipher = await self.get_cipher(c, cipher_id)
        cipher.folder_id = await self._folder_id(c, folder_id)
        cipher.favorite = favorite
        cipher.revision_date = await self._touch(c)
        await self.push(c, PushType.SYNC_CIPHER_UPDATE, cipher.id, cipher.revision_date)
        await self.db.commit()
        return cipher

    async def trash_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> None:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        now = await self._touch(c)
        for cipher in ciphers:
            if cipher.deleted_at is None:
                cipher.deleted_at = now
                cipher.revision_date = now
                await self._record(c, "vault.item.trash", "vault_item", cipher.id)
        await self._push_many(c, ciphers, PushType.SYNC_CIPHER_UPDATE, now)
        await self.db.commit()

    async def restore_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> list[VaultCipher]:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        now = await self._touch(c)
        for cipher in ciphers:
            if cipher.deleted_at is not None:
                cipher.deleted_at = None
                cipher.revision_date = now
                await self._record(c, "vault.item.restore", "vault_item", cipher.id)
        await self._push_many(c, ciphers, PushType.SYNC_CIPHER_UPDATE, now)
        await self.db.commit()
        return ciphers

    async def delete_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> None:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        now = await self._touch(c)
        await self._push_many(c, ciphers, PushType.SYNC_LOGIN_DELETE, now)
        for cipher in ciphers:
            await self._record(c, "vault.item.delete", "vault_item", cipher.id)
            await self.ciphers.delete(cipher)
        await self.db.commit()

    async def move_ciphers(self, c: VaultCaller, cipher_ids: list[UUID], folder_id: UUID | None) -> None:
        folder_id = await self._folder_id(c, folder_id)
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        now = await self._touch(c)
        for cipher in ciphers:
            cipher.folder_id = folder_id
            cipher.revision_date = now
        await self._push_many(c, ciphers, PushType.SYNC_CIPHER_UPDATE, now)
        await self.db.commit()

    async def create_folder(self, c: VaultCaller, name: str) -> VaultFolder:
        now = await self._touch(c)
        folder = self.folders.add(VaultFolder(user_id=c.account.user_id, name=name, revision_date=now))
        await self.db.flush()
        await self._record(c, "vault.folder.create", "vault_folder", folder.id)
        await self.push(c, PushType.SYNC_FOLDER_CREATE, folder.id, now)
        await self.db.commit()
        return folder

    async def get_folder(self, c: VaultCaller, folder_id: UUID) -> VaultFolder:
        folder = await self.folders.owned(c.account.user_id, folder_id)
        if folder is None:
            raise NotFoundError("Map niet gevonden")
        return folder

    async def update_folder(self, c: VaultCaller, folder_id: UUID, name: str) -> VaultFolder:
        folder = await self.get_folder(c, folder_id)
        folder.name = name
        folder.revision_date = await self._touch(c)
        await self._record(c, "vault.folder.update", "vault_folder", folder.id)
        await self.push(c, PushType.SYNC_FOLDER_UPDATE, folder.id, folder.revision_date)
        await self.db.commit()
        return folder

    async def delete_folder(self, c: VaultCaller, folder_id: UUID) -> None:
        folder = await self.get_folder(c, folder_id)
        now = await self._touch(c)
        await self._record(c, "vault.folder.delete", "vault_folder", folder.id)
        await self.push(c, PushType.SYNC_FOLDER_DELETE, folder.id, now)
        await self.folders.delete(folder)
        await self.db.commit()
