/** The short gesture guide (spec §10A A table), shown from the Touchpad page. */
import { GESTURES } from "../lib/gestures.ts";
import { Button } from "./ui.tsx";
import { Sheet } from "./Sheet.tsx";

export function GestureGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} title="Touchpad gestures" onClose={onClose} labelledBy="gesture-guide-title">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-text-faint uppercase tracking-wider">
            <th className="pb-2 pr-3">On the phone</th>
            <th className="pb-2">On the PC</th>
          </tr>
        </thead>
        <tbody>
          {GESTURES.map((g) => (
            <tr key={g.phone} className="border-t border-border align-top">
              <td className="py-2 pr-3 font-medium">{g.phone}</td>
              <td className="py-2 text-text-muted">{g.pc}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-text-faint mt-3">Adding a second finger never clicks. Scrolling, a cancelled touch (a call, rotation, switching apps) and ending a drag never add taps. Windows accepting input is shown as the live indicator; whether an app reacted is only visible on the PC.</p>
      <Button full className="mt-4" onClick={onClose}>
        Close
      </Button>
    </Sheet>
  );
}
