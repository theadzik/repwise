import { Alert, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { api, garminLoginSocket, when } from "./api";

export function Garmin() {
  const queries = useQueryClient();
  const connection = useQuery({ queryKey: ["garmin"], queryFn: api.connection });
  const refresh = () => queries.invalidateQueries({ queryKey: ["garmin"] });
  const signOut = useMutation({ mutationFn: api.signOutOfGarmin, onSuccess: refresh });

  return (
    <Stack>
      {connection.data ? (
        <Alert color="green" title="Signed in to Garmin">
          <Stack gap="xs" align="flex-start">
            <Text size="sm">Since {when(connection.data.connected_at)}.</Text>
            <Button
              color="red"
              variant="light"
              loading={signOut.isPending}
              onClick={() => signOut.mutate()}
            >
              Sign out of Garmin
            </Button>
          </Stack>
        </Alert>
      ) : (
        connection.isSuccess && (
          <Alert color="yellow" title="Not signed in to Garmin">
            Runs need a Garmin session. Sign in below.
          </Alert>
        )
      )}
      {connection.isError && <Alert color="red">{connection.error.message}</Alert>}
      {signOut.isError && <Alert color="red">{signOut.error.message}</Alert>}
      <Login onSignedIn={refresh} />
    </Stack>
  );
}

/** Where the page keeps a login waiting on its code, through a reload. */
const RESUME = "garmin-login-resume";

function stored(): string | null {
  try {
    return sessionStorage.getItem(RESUME);
  } catch {
    return null;
  }
}

function store(resume: string | null) {
  try {
    if (resume) sessionStorage.setItem(RESUME, resume);
    else sessionStorage.removeItem(RESUME);
  } catch {
    // Without it, a reload while the code is being fetched means starting again.
  }
}

/**
 * Both steps of a Garmin login: the password, then the code if Garmin asks
 * for one. The server keeps the login waiting for its code for a while, so a
 * phone that drops the connection while the code is read in the mail app has
 * not lost it: the code goes on a new connection, with the `resume` the
 * server gave.
 */
function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const socket = useRef<WebSocket | null>(null);
  const [wantsCode, setWantsCode] = useState(() => stored() !== null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => () => socket.current?.close(), []);

  /** A new connection, which says `first` and takes over from any before it. */
  function converse(first: object) {
    socket.current?.close();
    setWorking(true);
    setMessage(null);
    let settled = false;
    const opened = garminLoginSocket();
    socket.current = opened;
    opened.onopen = () => opened.send(JSON.stringify(first));
    opened.onmessage = (received) => {
      const said = JSON.parse(received.data);
      setWorking(false);
      if (said.step === "code_required") {
        store(said.resume);
        setWantsCode(true);
        setMessage("Garmin wants a code.");
      } else if (said.step === "code_rejected") {
        setMessage(said.detail);
      } else {
        settled = true;
        store(null);
        setWantsCode(false);
        setMessage(said.step === "connected" ? "Signed in to Garmin." : said.detail);
        if (said.step === "connected") onSignedIn();
      }
    };
    opened.onclose = () => {
      // Closed by a newer connection, or after the server's verdict.
      if (socket.current !== opened || settled) return;
      socket.current = null;
      setWorking(false);
      if (stored()) return; // The login is still waiting; the code can follow.
      setWantsCode(false);
      setMessage("The connection was lost. Start again.");
    };
  }

  function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const credentials = Object.fromEntries(new FormData(form));
    form.reset();
    store(null);
    converse(credentials);
  }

  function sendCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const code = Object.fromEntries(new FormData(form));
    form.reset();
    converse({ ...code, resume: stored() });
  }

  function startAgain() {
    socket.current?.close();
    store(null);
    setWantsCode(false);
    setMessage(null);
  }

  return (
    <Stack>
      {wantsCode ? (
        <form onSubmit={sendCode}>
          <Stack gap="xs">
            <TextInput
              name="code"
              label="Code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
            />
            <Button type="submit" loading={working}>
              Send code
            </Button>
            <Button variant="subtle" onClick={startAgain}>
              Start again
            </Button>
          </Stack>
        </form>
      ) : (
        <form onSubmit={signIn}>
          <Stack gap="xs">
            <TextInput name="email" type="email" label="Garmin email" autoComplete="username" required />
            <PasswordInput name="password" label="Garmin password" autoComplete="current-password" required />
            <Button type="submit" loading={working}>
              Sign in to Garmin
            </Button>
          </Stack>
        </form>
      )}
      {message && <Text size="sm">{message}</Text>}
    </Stack>
  );
}
