import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16 font-sans">
      <h1 className="text-3xl font-bold">VeriTrace</h1>
      <p className="mt-2 max-w-prose text-zinc-600">
        Consent-gated face verification + blockchain evidence. VeriTrace only
        searches for people who have enrolled themselves; it will never search
        for anyone else.
      </p>

      <nav className="mt-10 flex flex-col gap-3 sm:flex-row">
        <Link
          href="/enroll"
          className="rounded-lg bg-black px-5 py-3 text-center font-medium text-white hover:bg-zinc-800"
        >
          Enroll yourself
        </Link>
        <Link
          href="/scan"
          className="rounded-lg border border-zinc-300 px-5 py-3 text-center font-medium hover:bg-zinc-100"
        >
          Scan a face
        </Link>
      </nav>

      <footer className="mt-12 text-sm text-zinc-500">
        Phase 1 scaffold. The <code className="rounded bg-zinc-100 px-1 py-0.5">scan</code>{" "}
        endpoint is wired to the local Python CLI; enroll/search are gated behind
        owner consent + domain approval.
      </footer>
    </main>
  );
}
