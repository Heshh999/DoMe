/** Free/Pro comparison rows generated from the central plan contract (plans.json). */
import { PLANS } from "@dome/protocol";

export type Row = { label: string; free: string; pro: string };

export function pricingRows(): Row[] {
  const free = PLANS.plans.free;
  const pro = PLANS.plans.pro;
  const yes = "Included";
  const no = "—";
  return [
    { label: "Linked PCs that can be controlled", free: String(free.max_enabled_pcs), pro: `Up to ${pro.max_enabled_pcs}` },
    { label: "Paired phones per account", free: `Up to ${free.max_controllers}`, pro: `Up to ${pro.max_controllers}` },
    { label: "YouTube controls, Windows media, system volume", free: yes, pro: yes },
    { label: "Approved app launch, focus, minimise, close", free: yes, pro: yes },
    { label: "Lock and confirmed power controls", free: yes, pro: yes },
    { label: "Typed commands and keyboard dictation", free: yes, pro: yes },
    { label: "Secure access away from home through the DoMe service", free: yes, pro: yes },
    { label: "Permissions, revocation, security activity", free: yes, pro: yes },
    { label: "Custom remote layouts and named profiles", free: free.custom_layouts ? yes : no, pro: pro.custom_layouts ? "At paid launch" : no },
    { label: `Saved routines (up to ${pro.routine_max_steps} steps)`, free: free.routines ? yes : no, pro: pro.routines ? "At paid launch" : no },
    { label: "Ordinary manual controls", free: `${free.manual_command_rate_limit.per_minute} per minute`, pro: `${pro.manual_command_rate_limit.per_minute} per minute` },
  ];
}

