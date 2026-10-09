/** The short gesture guide (spec §10A A table), shown from the Touchpad page. */
import { Button } from "./ui.tsx";
import { Sheet } from "./Sheet.tsx";

export const GESTURES: Array<{ phone: string; pc: string }> = [
  { phone: "Slide one finger", pc: "Moves the cursor from where it is, like a laptop touchpad" },
  { phone: "Lift and put the finger down elsewhere", pc: "Keeps going from the current cursor position — no jump" },
  { phone: "Short, still tap", pc: "Left click" },
  { phone: "Two quick taps in the same spot", pc: "Double click (two clicks; Windows combines them)" },
  { phone: "Two-finger still tap, or the Right Click button", pc: "Right click" },
  { phone: "Two fingers moving", pc: "Scroll (vertical, and horizontal where the app supports it)" },
  { phone: "Drag mode, then move a finger", pc: "Holds the left button while moving; End Drag releases it" },
  { phone: "Keyboard button", pc: "Opens the phone keyboard and the key/shortcut rows" },
];

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
