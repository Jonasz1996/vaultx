"""Repositories: enkel data-toegang (queries), geen businessregels en geen commits."""

from app.repositories.audit import AuditRepository
from app.repositories.catalog import (
    ApplicationRepository,
    AppLoginRepository,
    AuthentikProtectionRepository,
    DiscoveredHostRepository,
    NpmChangeRepository,
    NpmConnectionRepository,
)
from app.repositories.links import LinkCollectionRepository, LinkRepository, LinkTagRepository
from app.repositories.memberships import MembershipRepository
from app.repositories.notes import (
    VaultNoteFileRepository,
    VaultNoteRepository,
    VaultNoteVersionRepository,
)
from app.repositories.organizations import OrganizationRepository, TeamRepository
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.repositories.vault import (
    VaultAccountRepository,
    VaultCipherRepository,
    VaultDeviceRepository,
    VaultFolderRepository,
)
from app.repositories.webauthn import WebAuthnCredentialRepository

__all__ = [
    "AppLoginRepository",
    "ApplicationRepository",
    "AuditRepository",
    "AuthentikProtectionRepository",
    "DiscoveredHostRepository",
    "LinkCollectionRepository",
    "LinkRepository",
    "LinkTagRepository",
    "MembershipRepository",
    "NpmChangeRepository",
    "NpmConnectionRepository",
    "OrganizationRepository",
    "SessionRepository",
    "TeamRepository",
    "UserRepository",
    "VaultAccountRepository",
    "VaultCipherRepository",
    "VaultDeviceRepository",
    "VaultFolderRepository",
    "WebAuthnCredentialRepository",
    "VaultNoteFileRepository",
    "VaultNoteRepository",
    "VaultNoteVersionRepository",
]
