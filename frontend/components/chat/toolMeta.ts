import {
  BookOpen,
  Cog,
  LifeBuoy,
  ReceiptText,
  ShieldCheck,
  UserCog,
  UserRound,
  type LucideIcon,
} from "lucide-react";

export const TOOL_META: Record<string, { icon: LucideIcon; name: string }> = {
  verify_customer: { icon: ShieldCheck, name: "Verify identity" },
  get_account_overview: { icon: UserRound, name: "Account & tier" },
  search_knowledge_base: { icon: BookOpen, name: "Knowledge base" },
  issue_refund: { icon: ReceiptText, name: "Refund" },
  update_account: { icon: UserCog, name: "Update account" },
  create_escalation_ticket: { icon: LifeBuoy, name: "Escalation" },
};

export function toolMeta(tool: string): { icon: LucideIcon; name: string } {
  return TOOL_META[tool] ?? { icon: Cog, name: tool };
}
