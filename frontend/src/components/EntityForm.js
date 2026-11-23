import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { ErrorBox } from "./ui";
function slugify(v) {
    return v
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[̀-ͯ]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 63);
}
/** Formulier voor organisatie of team. In bewerkmodus is de slug vast (wordt gebruikt in Authentik-groepen). */
export function EntityForm({ initial, editing = false, onSubmit, error, pending, slugHelp, }) {
    const [name, setName] = useState(initial?.name ?? "");
    const [slug, setSlug] = useState(initial?.slug ?? "");
    const [slugTouched, setSlugTouched] = useState(editing);
    const [description, setDescription] = useState(initial?.description ?? "");
    const submit = (e) => {
        e.preventDefault();
        onSubmit({ name, slug, description });
    };
    return (_jsxs("form", { onSubmit: submit, className: "form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { required: true, value: name, onChange: (e) => {
                            setName(e.target.value);
                            if (!slugTouched)
                                setSlug(slugify(e.target.value));
                        } })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Slug" }), _jsx("input", { required: true, pattern: "[a-z0-9]([a-z0-9\\-]{0,61}[a-z0-9])?", value: slug, disabled: editing, onChange: (e) => {
                            setSlugTouched(true);
                            setSlug(e.target.value);
                        } }), slugHelp && _jsx("small", { className: "muted", children: slugHelp })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Omschrijving" }), _jsx("textarea", { rows: 3, value: description, onChange: (e) => setDescription(e.target.value) })] }), _jsx(ErrorBox, { error: error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: pending, children: editing ? "Opslaan" : "Aanmaken" }) })] }));
}
