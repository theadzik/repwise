// The API, as the page uses it. The shapes come from schema.d.ts, which is
// generated from the API's own description: `npm run types`.
import type { components } from "./schema";

export type Athlete = components["schemas"]["AthleteOut"];
export type Run = components["schemas"]["RunOut"];
export type RunSummary = components["schemas"]["RunSummary"];
export type Connection = components["schemas"]["ConnectionOut"];

/** An answer that was not a success, with what the API said about it. */
export class Problem extends Error {
  constructor(
    readonly status: number,
    detail: string,
  ) {
    super(detail);
  }
}

async function call<T>(method: string, path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    // The API refuses a change that is not labelled as JSON, which is what
    // stops another site's page from making one with our cookie.
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  // oauth2-proxy answers a request that asks for JSON with 401 once the
  // session has run out, where it would redirect a page. Loading the page
  // again is what takes that redirect, to the sign-in and back.
  if (response.status === 401) {
    location.reload();
    await new Promise(() => {});
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Problem(response.status, body.detail ?? response.statusText);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

/** The answer, or null where the API says there is no such thing yet. */
async function orNothing<T>(found: Promise<T>): Promise<T | null> {
  try {
    return await found;
  } catch (error) {
    if (error instanceof Problem && error.status === 404) return null;
    throw error;
  }
}

export const api = {
  me: () => orNothing(call<Athlete>("GET", "/me")),
  register: () => call<Athlete>("PUT", "/me"),
  runs: () => call<RunSummary[]>("GET", "/runs"),
  run: (id: string) => call<Run>("GET", `/runs/${id}`),
  startRun: () => call<Run>("POST", "/runs"),
  connection: () => orNothing(call<Connection>("GET", "/garmin/connection")),
  signOutOfGarmin: () => call<void>("DELETE", "/garmin/connection"),
};

/** Where signing out of the site goes. oauth2-proxy answers it, not the API. */
export const SIGN_OUT = "/oauth2/sign_out?rd=%2F";

/** The GitHub account oauth2-proxy has signed in, if it will say. */
export async function signedInAs(): Promise<string | null> {
  try {
    const response = await fetch("/oauth2/userinfo", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const session = await response.json();
    return session.user ?? null;
  } catch {
    return null;
  }
}

export function garminLoginSocket(): WebSocket {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${scheme}://${location.host}/api/v1/garmin/connection/login`);
}

export const ACTIVE = new Set(["queued", "running"]);

export function when(timestamp: string): string {
  return new Date(timestamp).toLocaleString();
}
