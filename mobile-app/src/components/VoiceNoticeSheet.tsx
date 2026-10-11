/**
 * The one-time "Talk to DoMe" notice (docs/design/assistant.md §4.7), shown before the microphone is used for the
 * first time, whether or not the AI assistant is on. Its Continue tap is the gesture that starts listening (marked as a
 * talk button, so the same tap never also unlocks speech).
 */
import { Sheet } from "./Sheet.tsx";
import { Button } from "./ui.tsx";

export function VoiceNoticeSheet({ open, onContinue, onDictate, onClose }: { open: boolean; onContinue(): void; onDictate(): void; onClose(): void }) {
  return (
    <Sheet open={open} title="Talk to DoMe" onClose={onClose} labelledBy="voice-notice-title">
      <div className="space-y-2 text-sm text-text-muted">
        <p>DoMe listens only while you tap the microphone button, and stops when you tap it again, after 15 seconds, or when you leave the app.</p>
        <p>Your iPhone's speech recognition, provided by Apple, turns your words into text. Apple may process them on its servers.</p>
        <p>DoMe never records or keeps audio.</p>
        <p>Prefer not to? Use Dictate: the microphone key on your iPhone's keyboard.</p>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button onClick={onDictate}>Dictate instead</Button>
        {/* starts the microphone: like the talk button, never the tap that unlocks speech */}
        <Button variant="primary" onClick={onContinue} data-talk-button="">
          Continue
        </Button>
      </div>
    </Sheet>
  );
}
