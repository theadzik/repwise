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

/**
 * Both steps of a Garmin login, over one connection: the password, then the
 * code if Garmin asks for one. The server keeps nothing between the steps, so
 * a connection that drops means starting again.
 */
function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const socket = useRef<WebSocket | null>(null);
  // Set once the server has given its verdict, so that the close which
  // follows is not taken for a lost connection.
  const settled = useRef(false);
  const [wantsCode, setWantsCode] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => () => socket.current?.close(), []);

  function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const credentials = Object.fromEntries(new FormData(form));
    form.reset();
    setWorking(true);
    setMessage(null);
    settled.current = false;

    const opened = garminLoginSocket();
    socket.current = opened;
    opened.onopen = () => opened.send(JSON.stringify(credentials));
    opened.onmessage = (received) => {
      const said = JSON.parse(received.data);
      setWorking(false);
      if (said.step === "code_required") {
        setWantsCode(true);
        setMessage("Garmin wants a code.");
      } else if (said.step === "code_rejected") {
        setMessage(said.detail);
      } else {
        settled.current = true;
        setWantsCode(false);
        setMessage(said.step === "connected" ? "Signed in to Garmin." : said.detail);
        if (said.step === "connected") onSignedIn();
      }
    };
    opened.onclose = () => {
      socket.current = null;
      if (settled.current) return;
      setWorking(false);
      setWantsCode(false);
      setMessage("The connection was lost. Start again.");
    };
  }

  function sendCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    socket.current?.send(JSON.stringify(Object.fromEntries(new FormData(form))));
    form.reset();
    setWorking(true);
  }

  return (
    // A form, so no wider than it was when the whole page was this wide.
    <Stack maw="var(--container-size-xs)">
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
