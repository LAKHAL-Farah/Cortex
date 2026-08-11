"use client";

import type {
  FormEvent,
  KeyboardEvent,
} from "react";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  Bot,
  Database,
  Download,
  FileText,
  LoaderCircle,
  Send,
  ShieldCheck,
  Sparkles,
  User,
} from "lucide-react";


type KnowledgeResult = {
  point_id: string;
  score: number;
  document_id: string;
  title: string;
  text: string;
  source_path: string;
  chunk_index: number;
  token_count: number;
  service: string;
  environment: string;
  criticality: string;
  document_type: string;
  language: string;
};

type SearchResponse = {
  query: string;
  result_count: number;
  results: KnowledgeResult[];
};

type KnowledgeHealth = {
  status: string;
  collection: string;
  collection_status: string;
  points_count: number;
  embedding_model: string;
  embedding_device: string;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  results?: KnowledgeResult[];
  isError?: boolean;
};


const SUGGESTED_QUESTIONS = [
  {
    label: "Cinder & RabbitMQ",
    question:
      "Comment redémarrer cinder-backup après une panne RabbitMQ ?",
  },
  {
    label: "Logs compute1",
    question:
      "Comment vérifier que compute1 envoie ses logs dans Loki ?",
  },
  {
    label: "Prometheus",
    question:
      "Un nœud ne remonte plus dans Prometheus. Comment vérifier node_exporter ?",
  },
];


function createMessageId(): string {
  return `${Date.now()}-${Math.random()
    .toString(36)
    .slice(2)}`;
}


function downloadPassage(
  result: KnowledgeResult,
): void {
  const extension = result.source_path.endsWith(".txt")
    ? "txt"
    : "md";

  const content = [
    `# ${result.title}`,
    "",
    result.text,
    "",
    "---",
    `Source : ${result.source_path}`,
    `Service : ${result.service}`,
    `Score : ${(result.score * 100).toFixed(2)} %`,
  ].join("\n");

  const blob = new Blob(
    [content],
    {
      type: "text/plain;charset=utf-8",
    },
  );

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download =
    `${result.document_id}.${extension}`;

  document.body.appendChild(link);
  link.click();
  link.remove();

  URL.revokeObjectURL(url);
}


export default function CopilotPage() {
  const [question, setQuestion] = useState("");
  const [service, setService] = useState("");
  const [loading, setLoading] = useState(false);

  const [health, setHealth] =
    useState<KnowledgeHealth | null>(null);

  const [healthError, setHealthError] =
    useState<string | null>(null);

  const [messages, setMessages] =
    useState<ChatMessage[]>([
      {
        id: "welcome",
        role: "assistant",
        content:
          "Bonjour, je suis Cortex AI Copilot. Posez-moi une question sur les runbooks Cinder, Loki ou Prometheus.",
      },
    ]);

  const bottomRef = useRef<HTMLDivElement | null>(
    null,
  );

  useEffect(() => {
    async function loadHealth() {
      try {
        const response = await fetch(
          "/api/knowledge/health",
          {
            cache: "no-store",
          },
        );

        const data = await response.json();

        if (!response.ok) {
          throw new Error(
            data.detail ||
              "Knowledge service unavailable.",
          );
        }

        setHealth(data);
        setHealthError(null);
      } catch (error) {
        setHealthError(
          error instanceof Error
            ? error.message
            : "Knowledge service unavailable.",
        );
      }
    }

    void loadHealth();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  async function submitQuestion(
    rawQuestion: string,
  ): Promise<void> {
    const normalizedQuestion =
      rawQuestion.trim();

    if (!normalizedQuestion || loading) {
      return;
    }

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: "user",
      content: normalizedQuestion,
    };

    setMessages((current) => [
      ...current,
      userMessage,
    ]);

    setQuestion("");
    setLoading(true);

    try {
      const response = await fetch(
        "/api/knowledge/search",
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json",
          },
          body: JSON.stringify({
            query: normalizedQuestion,
            limit: 3,
            service:
              service || null,
            environment: "production",
            document_type: "runbook",
            language: "fr",
            minimum_score: 0.84,
          }),
        },
      );

      const data =
        (await response.json()) as
          | SearchResponse
          | { detail?: string };

      if (!response.ok) {
        throw new Error(
          "detail" in data && data.detail
            ? data.detail
            : "La recherche a échoué.",
        );
      }

      const searchData =
        data as SearchResponse;

      const assistantMessage: ChatMessage = {
        id: createMessageId(),
        role: "assistant",
        content:
          searchData.result_count > 0
            ? `J'ai trouvé ${searchData.result_count} source(s). Voici les procédures les plus pertinentes.`
            : "Aucun runbook suffisamment pertinent n'a été trouvé.",
        results: searchData.results,
      };

      setMessages((current) => [
        ...current,
        assistantMessage,
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: "assistant",
          content:
            error instanceof Error
              ? error.message
              : "Une erreur inattendue est survenue.",
          isError: true,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    void submitQuestion(question);
  }

  function handleKeyDown(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) {
    if (
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();
      void submitQuestion(question);
    }
  }

  return (
    <main className="grid gap-4">
      <section className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="eyebrow">
            Artificial intelligence
          </div>

          <h2 className="font-display mt-1 text-lg font-semibold text-color-text">
            Cortex AI Copilot
          </h2>

          <p className="mt-1 max-w-2xl text-sm leading-6 text-text-faint">
            Recherchez les procédures
            d'exploitation stockées dans la
            base de connaissances vectorielle
            Cortex.
          </p>
        </div>

        <div
          className="inline-flex items-center gap-2 rounded-[var(--radius-control)] px-3.5 py-2 text-sm"
          style={{
            border:
              "1px solid var(--border)",
          }}
        >
          <span
            className="status-dot"
            style={{
              background:
                health?.status === "ok"
                  ? "var(--ok)"
                  : "var(--crit)",
            }}
          />

          {health?.status === "ok"
            ? "Knowledge Base online"
            : "Knowledge Base offline"}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="panel flex min-h-[680px] flex-col overflow-hidden">
          <div
            className="flex items-center justify-between border-b px-5 py-4"
            style={{
              borderColor:
                "var(--border-soft)",
            }}
          >
            <div className="flex items-center gap-3">
              <div
                className="grid h-10 w-10 place-items-center rounded-xl"
                style={{
                  background:
                    "var(--accent-soft)",
                  color: "var(--accent)",
                }}
              >
                <Bot className="h-5 w-5" />
              </div>

              <div>
                <div className="text-sm font-semibold text-color-text">
                  Runbook assistant
                </div>

                <div className="text-xs text-text-faint">
                  Qdrant + multilingual-e5-base
                </div>
              </div>
            </div>

            <select
              value={service}
              onChange={(event) =>
                setService(event.target.value)
              }
              className="rounded-[var(--radius-control)] px-3 py-2 text-sm outline-none"
              style={{
                background: "var(--canvas)",
                border:
                  "1px solid var(--border)",
                color: "var(--text)",
              }}
            >
              <option value="">
                Tous les services
              </option>

              <option value="cinder">
                Cinder
              </option>

              <option value="loki">
                Loki
              </option>

              <option value="prometheus">
                Prometheus
              </option>
            </select>
          </div>

          <div className="flex-1 space-y-5 overflow-y-auto p-5">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex gap-3 ${
                  message.role === "user"
                    ? "justify-end"
                    : "justify-start"
                }`}
              >
                {message.role ===
                  "assistant" && (
                  <div
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-full"
                    style={{
                      background:
                        "var(--accent-soft)",
                      color: "var(--accent)",
                    }}
                  >
                    <Bot className="h-4 w-4" />
                  </div>
                )}

                <div
                  className="max-w-[88%] space-y-3"
                >
                  <div
                    className="rounded-2xl px-4 py-3 text-sm leading-6"
                    style={
                      message.role === "user"
                        ? {
                            background:
                              "var(--accent)",
                            color: "white",
                          }
                        : {
                            background:
                              message.isError
                                ? "rgba(239, 68, 68, 0.10)"
                                : "var(--canvas)",
                            border:
                              "1px solid var(--border-soft)",
                            color:
                              "var(--text)",
                          }
                    }
                  >
                    {message.content}
                  </div>

                  {message.results?.map(
                    (result, index) => (
                      <article
                        key={result.point_id}
                        className="overflow-hidden rounded-xl"
                        style={{
                          border:
                            "1px solid var(--border)",
                          background:
                            "var(--surface)",
                        }}
                      >
                        <div
                          className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-start sm:justify-between"
                          style={{
                            borderColor:
                              "var(--border-soft)",
                          }}
                        >
                          <div>
                            <div className="flex items-center gap-2">
                              <FileText className="h-4 w-4 text-text-faint" />

                              <h3 className="text-sm font-semibold text-color-text">
                                {result.title}
                              </h3>
                            </div>

                            <div className="mt-2 flex flex-wrap gap-2 text-xs text-text-faint">
                              <span>
                                Source :{" "}
                                {result.source_path}
                              </span>

                              <span>·</span>

                              <span>
                                Service :{" "}
                                {result.service}
                              </span>

                              <span>·</span>

                              <span>
                                Chunk{" "}
                                {result.chunk_index}
                              </span>
                            </div>
                          </div>

                          <div
                            className="shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold"
                            style={{
                              background:
                                "var(--accent-soft)",
                              color:
                                "var(--accent)",
                            }}
                          >
                            {(
                              result.score * 100
                            ).toFixed(2)}
                            %
                          </div>
                        </div>

                        <details
                          open={index === 0}
                          className="group"
                        >
                          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-color-text">
                            Voir la procédure
                          </summary>

                          <div className="px-4 pb-4">
                            <div
                              className="whitespace-pre-wrap rounded-lg p-4 text-sm leading-7 text-text-faint"
                              style={{
                                background:
                                  "var(--canvas)",
                              }}
                            >
                              {result.text}
                            </div>

                            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                              <div className="text-xs text-text-faint">
                                {
                                  result.token_count
                                }{" "}
                                tokens ·{" "}
                                {
                                  result.environment
                                }
                              </div>

                              <button
                                type="button"
                                onClick={() =>
                                  downloadPassage(
                                    result,
                                  )
                                }
                                className="inline-flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-xs font-medium"
                                style={{
                                  border:
                                    "1px solid var(--border)",
                                  color:
                                    "var(--text)",
                                }}
                              >
                                <Download className="h-3.5 w-3.5" />
                                Télécharger
                              </button>
                            </div>
                          </div>
                        </details>
                      </article>
                    ),
                  )}
                </div>

                {message.role === "user" && (
                  <div
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-full"
                    style={{
                      background:
                        "var(--canvas)",
                      border:
                        "1px solid var(--border)",
                    }}
                  >
                    <User className="h-4 w-4 text-text-faint" />
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="flex items-center gap-3 text-sm text-text-faint">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                Cortex recherche dans les
                runbooks…
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          <form
            onSubmit={handleSubmit}
            className="border-t p-4"
            style={{
              borderColor:
                "var(--border-soft)",
            }}
          >
            <div
              className="flex items-end gap-3 rounded-xl p-2"
              style={{
                background: "var(--canvas)",
                border:
                  "1px solid var(--border)",
              }}
            >
              <textarea
                value={question}
                onChange={(event) =>
                  setQuestion(
                    event.target.value,
                  )
                }
                onKeyDown={handleKeyDown}
                rows={2}
                placeholder="Posez une question sur l'infrastructure OpenStack…"
                className="min-h-[54px] flex-1 resize-none bg-transparent px-2 py-2 text-sm text-color-text outline-none"
              />

              <button
                type="submit"
                disabled={
                  loading ||
                  !question.trim()
                }
                className="grid h-10 w-10 place-items-center rounded-lg text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                style={{
                  background:
                    "var(--accent)",
                }}
              >
                <Send className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-2 text-xs text-text-faint">
              Entrée pour envoyer ·
              Maj + Entrée pour une nouvelle
              ligne
            </div>
          </form>
        </section>

        <aside className="grid content-start gap-4">
          <section className="panel p-5">
            <div className="eyebrow">
              Knowledge status
            </div>

            <h3 className="mt-1 text-[15px] font-semibold text-color-text">
              Base vectorielle
            </h3>

            {health ? (
              <div className="mt-4 space-y-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-text-faint">
                    Collection
                  </span>

                  <span className="font-medium text-color-text">
                    {health.collection}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-faint">
                    Documents
                  </span>

                  <span className="font-medium text-color-text">
                    {health.points_count}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-faint">
                    Modèle
                  </span>

                  <span className="max-w-[170px] truncate font-medium text-color-text">
                    {health.embedding_model}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-faint">
                    Device
                  </span>

                  <span className="font-medium text-color-text">
                    {health.embedding_device}
                  </span>
                </div>
              </div>
            ) : (
              <div className="mt-4 text-sm text-text-faint">
                {healthError ||
                  "Chargement…"}
              </div>
            )}
          </section>

          <section className="panel p-5">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-text-faint" />

              <h3 className="text-sm font-semibold text-color-text">
                Questions suggérées
              </h3>
            </div>

            <div className="mt-4 grid gap-2">
              {SUGGESTED_QUESTIONS.map(
                (suggestion) => (
                  <button
                    key={suggestion.label}
                    type="button"
                    onClick={() =>
                      setQuestion(
                        suggestion.question,
                      )
                    }
                    className="rounded-lg p-3 text-left text-sm transition-opacity hover:opacity-80"
                    style={{
                      background:
                        "var(--canvas)",
                      border:
                        "1px solid var(--border-soft)",
                      color: "var(--text)",
                    }}
                  >
                    <div className="font-medium">
                      {suggestion.label}
                    </div>

                    <div className="mt-1 text-xs leading-5 text-text-faint">
                      {suggestion.question}
                    </div>
                  </button>
                ),
              )}
            </div>
          </section>

          <section className="panel p-5">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-text-faint" />

              <h3 className="text-sm font-semibold text-color-text">
                Sécurité
              </h3>
            </div>

            <p className="mt-3 text-xs leading-5 text-text-faint">
              La clé Cortex reste côté serveur
              Next.js. Elle n'est jamais
              transmise au navigateur.
            </p>

            <div className="mt-4 flex items-center gap-2 text-xs text-text-faint">
              <Database className="h-3.5 w-3.5" />
              Qdrant Cloud
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}