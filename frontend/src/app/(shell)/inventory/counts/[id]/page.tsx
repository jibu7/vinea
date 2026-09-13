"use client";

import { useParams } from "next/navigation";
import { CountSheetScreen } from "@/features/inventory/count-sheet-screen";

export default function InventoryCountSheetPage() {
  const params = useParams<{ id: string }>();
  return <CountSheetScreen sessionId={Number(params.id)} />;
}
