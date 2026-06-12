import PubRouteClient from "./pub-route-client";

export function generateStaticParams() {
  return [{ slug: [] as string[] }, { slug: ["cluster-dashboard"] }];
}

export default async function PubCatchAllPage({
  params,
}: {
  params: Promise<{ slug?: string[] }>;
}) {
  const { slug } = await params;
  return <PubRouteClient slug={slug} />;
}
