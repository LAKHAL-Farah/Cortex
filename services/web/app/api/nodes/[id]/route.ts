// web/app/api/nodes/[id]/route.ts
export async function DELETE(_req: Request, { params }: { params: { id: string } }) {
  const res = await fetch(`${process.env.CORTEX_API_URL}/api/v1/nodes/${params.id}`, {
    method: "DELETE",
    headers: { "X-API-Key": process.env.CORTEX_API_KEY! },
  });
  return new Response(null, { status: res.status });
}