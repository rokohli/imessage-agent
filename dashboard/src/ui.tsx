import { useEffect, useRef, useState, type ReactNode } from "react";
export const time = (value: number, zone?: string) =>
  new Date(value * 1000).toLocaleString(undefined, {
    timeZone: zone,
    dateStyle: "medium",
    timeStyle: "short",
  });
export function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <span aria-hidden="true">◌</span>
      <p>{children}</p>
    </div>
  );
}
export function Card({
  title,
  children,
  action,
}: {
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="card">
      {title && (
        <div className="card-heading">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function useData<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const current = useRef(0);
  const refresh = async () => {
    const id = ++current.current;
    setLoading(true);
    setError("");
    try {
      const value = await load();
      if (id === current.current) setData(value);
    } catch (e) {
      if (id === current.current)
        setError(e instanceof Error ? e.message : "Unable to load data");
    } finally {
      if (id === current.current) setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
    return () => {
      current.current++;
    };
  }, []);
  return { data, error, loading, refresh };
}
export function LoadState({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error: string;
  retry: () => void;
}) {
  return (
    <>
      {loading && (
        <p role="status" className="muted">
          Loading local data…
        </p>
      )}
      {error && (
        <div className="error" role="alert">
          {error} <button onClick={retry}>Try again</button>
        </div>
      )}
    </>
  );
}
export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    dialog.current?.showModal();
    return () => {
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      aria-label={title}
    >
      <div className="card-heading">
        <h2>{title}</h2>
        <button aria-label="Close dialog" onClick={close}>
          ✕
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function ErrorText({ error }: { error: string }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null;
}
export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "The operation failed";
}
