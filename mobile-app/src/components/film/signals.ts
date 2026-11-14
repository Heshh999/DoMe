/**
 * What the server says, for the demo film's labels (timeline.ts FilmSignals): the pages that show the film (home, /demo)
 * read it once and provide it; the exported video and anything outside a provider get the all-off wording.
 */
import { createContext, useContext } from "react";

import { useScreenSwitch } from "../../app/hooks.ts";
import { aiSwitchedOn, sellsPlan, type BillingAvailability } from "../../lib/useBillingAvailability.ts";
import { FILM_SIGNALS_OFF, type FilmSignals } from "./timeline.ts";

export const FilmSignalsContext = createContext<FilmSignals>(FILM_SIGNALS_OFF);

export function useFilmSignals(): FilmSignals {
  return useContext(FilmSignalsContext);
}

/** The film's signals from GET /v1/plans (`availability`, null until it answers) and GET /healthz `screen_view`. */
export function filmSignalsFrom(availability: BillingAvailability | null, screenOn: boolean): FilmSignals {
  return { screenOn, aiOn: aiSwitchedOn(availability), proSold: sellsPlan(availability, "pro"), proPlusSold: sellsPlan(availability, "pro_plus") };
}

/** For a page that shows the film: the server's answers, each "off" until it arrives (and when it fails). */
export function useServerFilmSignals(availability: BillingAvailability | null): FilmSignals {
  const screenOn = useScreenSwitch() === "on";
  return filmSignalsFrom(availability, screenOn);
}
