export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-4 px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">DocuLens</h1>
      <p className="text-lg text-slate-600">
        Upload PDF documents, organise them into collections and ask questions that are answered
        with citable evidence.
      </p>
      <p role="status" className="text-sm text-slate-500">
        The application interface is not available yet. This page verifies the frontend build.
      </p>
    </main>
  );
}
