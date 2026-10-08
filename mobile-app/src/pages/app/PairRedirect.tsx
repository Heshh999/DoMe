import { Navigate, useLocation } from "react-router";

/** `/pair#code=…` deep link from the PC's QR code: hand the fragment to the pairing page untouched. */
export function PairRedirect() {
  const loc = useLocation();
  return <Navigate to={{ pathname: "/app/devices/pair", hash: loc.hash }} replace />;
}

