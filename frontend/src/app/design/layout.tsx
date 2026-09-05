import { ToastProvider } from "@/design/components/toast";

export default function DesignLayout({ children }: { children: React.ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}
