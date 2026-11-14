import { Button, Stack, Text } from "@mantine/core";

import { type Athlete, SIGN_OUT, when } from "./api";

export function Account({ athlete }: { athlete: Athlete }) {
  return (
    <Stack align="flex-start">
      <Text>
        Signed in with GitHub as <b>{athlete.github_login}</b>.
      </Text>
      <Text size="sm" c="dimmed">
        Registered {when(athlete.created_at)}.
      </Text>
      {/* A link, not a request: oauth2-proxy holds the session and ends it. */}
      <Button component="a" href={SIGN_OUT} color="red" variant="light">
        Sign out
      </Button>
      <Text size="sm" c="dimmed">
        This ends the session in this browser. It does not sign you out of GitHub, or of Garmin.
      </Text>
    </Stack>
  );
}
