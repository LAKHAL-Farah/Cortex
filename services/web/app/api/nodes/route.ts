export async function POST(req: Request) {
  const body = await req.json();
  const res = await fetch(`${process.env.CORTEX_API_URL}/api/v1/nodes`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": process.env.CORTEX_API_KEY!,   // never sent to the browser
    },
    body: JSON.stringify(body),
  });
  return Response.json(await res.json(), { status: res.status });
}