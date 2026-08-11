import { NextResponse } from "next/server";

const API_URL =
  process.env.CORTEX_KNOWLEDGE_API_URL ||
  process.env.CORTEX_API_URL;

const API_KEY = process.env.CORTEX_API_KEY;

export async function GET() {
  if (!API_URL || !API_KEY) {
    return NextResponse.json(
      {
        detail:
          "CORTEX_KNOWLEDGE_API_URL ou CORTEX_API_KEY n'est pas configuré.",
      },
      { status: 500 },
    );
  }

  try {
    const response = await fetch(
      `${API_URL}/api/v1/knowledge/health`,
      {
        headers: {
          "X-API-Key": API_KEY,
        },
        cache: "no-store",
      },
    );

    const data = await response.json().catch(() => ({
      detail: "Réponse invalide du backend Cortex.",
    }));

    return NextResponse.json(data, {
      status: response.status,
    });
  } catch (error) {
    console.error("Knowledge health proxy error:", error);

    return NextResponse.json(
      {
        detail:
          "Impossible de contacter le service Knowledge Cortex.",
      },
      { status: 502 },
    );
  }
}
