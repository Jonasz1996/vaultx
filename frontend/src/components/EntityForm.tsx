import { useState, type FormEvent } from "react";
import { ErrorBox } from "./ui";

export interface EntityValues {
  slug: string;
  name: string;
  description: string;
}

function slugify(v: string) {
  return v
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

/** Formulier voor organisatie of team. In bewerkmodus is de slug vast (wordt gebruikt in Authentik-groepen). */
export function EntityForm({
  initial,
  editing = false,
  onSubmit,
  error,
  pending,
  slugHelp,
}: {
  initial?: Partial<EntityValues>;
  editing?: boolean;
  onSubmit: (v: EntityValues) => void;
  error: unknown;
  pending: boolean;
  slugHelp?: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [slugTouched, setSlugTouched] = useState(editing);
  const [description, setDescription] = useState(initial?.description ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({ name, slug, description });
  };
  return (
    <form onSubmit={submit} className="form">
      <label className="field">
        <span>Naam</span>
        <input
          required
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (!slugTouched) setSlug(slugify(e.target.value));
          }}
        />
      </label>
      <label className="field">
        <span>Slug</span>
        <input
          required
          pattern="[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?"
          value={slug}
          disabled={editing}
          onChange={(e) => {
            setSlugTouched(true);
            setSlug(e.target.value);
          }}
        />
        {slugHelp && <small className="muted">{slugHelp}</small>}
      </label>
      <label className="field">
        <span>Omschrijving</span>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <ErrorBox error={error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={pending}>
          {editing ? "Opslaan" : "Aanmaken"}
        </button>
      </div>
    </form>
  );
}
