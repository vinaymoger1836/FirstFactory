import { useEffect, useId, useRef, useState } from "react";

/** Modal dialog asking for a note (re-run or reject). Native <dialog>: focus trap and Escape. */
export function NoteDialog({
  open,
  title,
  intro,
  confirm,
  required,
  danger,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  intro: string;
  confirm: string;
  required?: boolean;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (note: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState("");
  const id = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setNote("");
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog ref={ref} className="dialog" aria-labelledby={`${id}-t`} onClose={onClose}>
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          if (required && !note.trim()) return;
          onConfirm(note.trim());
        }}
      >
        <h2 id={`${id}-t`}>{title}</h2>
        <p className="muted">{intro}</p>
        <label htmlFor={`${id}-n`} className="field__label">
          Note for the agent{required ? " (required)" : " (optional)"}
        </label>
        <textarea
          id={`${id}-n`}
          className="input textarea"
          rows={4}
          maxLength={2000}
          required={required}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="dialog__actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={required && !note.trim()}>
            {confirm}
          </button>
        </div>
      </form>
    </dialog>
  );
}
