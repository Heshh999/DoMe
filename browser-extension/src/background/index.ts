/** Service worker entry. Everything else is in service.ts so tests can drive it with a chrome stub. */
import type { ExtensionApi } from "./api.ts";
import { createBackground } from "./service.ts";

const background = createBackground(chrome as unknown as ExtensionApi);
void background.start();
