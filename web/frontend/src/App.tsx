import { Alert, Button, Center, Container, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Account } from "./Account";
import { api, SIGN_OUT, signedInAs } from "./api";
import { Garmin } from "./Garmin";
import { Runs } from "./Runs";

export function App() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });

  if (me.isPending) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }
  if (me.isError) {
    return (
      <Page>
        <Alert color="red" title="Could not reach repwise">
          {me.error.message}
        </Alert>
      </Page>
    );
  }
  if (me.data === null) {
    return (
      <Page>
        <Register />
      </Page>
    );
  }
  return (
    <Page>
      <Tabs defaultValue="runs" keepMounted={false}>
        <Tabs.List grow>
          <Tabs.Tab value="runs">Runs</Tabs.Tab>
          <Tabs.Tab value="garmin">Garmin</Tabs.Tab>
          <Tabs.Tab value="account">Account</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="runs" pt="md">
          <Runs />
        </Tabs.Panel>
        <Tabs.Panel value="garmin" pt="md">
          <Garmin />
        </Tabs.Panel>
        <Tabs.Panel value="account" pt="md">
          <Account athlete={me.data} />
        </Tabs.Panel>
      </Tabs>
    </Page>
  );
}

function Page({ children }: { children: ReactNode }) {
  return (
    <Container size="xs" py="md">
      <Stack>
        <Title order={2}>repwise</Title>
        {children}
      </Stack>
    </Container>
  );
}

/** The first visit: signed in with GitHub, and not yet an athlete here. */
function Register() {
  const queries = useQueryClient();
  const github = useQuery({ queryKey: ["signed-in-as"], queryFn: signedInAs });
  const register = useMutation({
    mutationFn: api.register,
    onSuccess: (athlete) => queries.setQueryData(["me"], athlete),
  });

  return (
    <Stack>
      <Text>
        {github.data ? `Signed in with GitHub as ${github.data}. ` : "Signed in with GitHub. "}
        There is no repwise account for it yet.
      </Text>
      <Button size="lg" loading={register.isPending} onClick={() => register.mutate()}>
        Create account
      </Button>
      {register.isError && <Alert color="red">{register.error.message}</Alert>}
      <Button component="a" href={SIGN_OUT} variant="subtle">
        Sign out
      </Button>
    </Stack>
  );
}
