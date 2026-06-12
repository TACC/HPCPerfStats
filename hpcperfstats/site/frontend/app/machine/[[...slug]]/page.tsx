import MachineRouteClient from "./machine-route-client";

export function generateStaticParams() {
  return [
    { slug: [] as string[] },
    { slug: ["jobs"] },
    { slug: ["admin_monitor"] },
    { slug: ["job_monitor"] },
    { slug: ["api-key"] },
  ];
}

export default async function MachineCatchAllPage({
  params,
}: {
  params: Promise<{ slug?: string[] }>;
}) {
  const { slug } = await params;
  return <MachineRouteClient slug={slug} />;
}
