import { Alert, Badge, Button, Code, Group, Paper, Stack, Text, UnstyledButton } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ACTIVE, api, type RunSummary, when } from "./api";

const COLOUR: Record<string, string> = {
  queued: "gray",
  running: "blue",
  succeeded: "green",
  failed: "red",
};
/** How often to ask again while a run is queued or running. */
const POLL = 2000;

export function Runs() {
  const queries = useQueryClient();
  // Which run to show: the one picked from the history, or else the newest.
  const [picked, setPicked] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: (query) =>
      query.state.data?.some((run) => ACTIVE.has(run.status)) ? POLL : false,
  });
  const newest = runs.data?.[0];
  const shown = picked ?? newest?.id ?? null;
  const run = useQuery({
    queryKey: ["run", shown],
    queryFn: () => api.run(shown!),
    enabled: shown !== null,
    refetchInterval: (query) =>
      query.state.data && ACTIVE.has(query.state.data.status) ? POLL : false,
  });

  const start = useMutation({
    mutationFn: api.startRun,
    // Whether it was accepted or one was already going, the list is what
    // shows it.
    onSettled: () => {
      setPicked(null);
      return queries.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const going = newest !== undefined && ACTIVE.has(newest.status);

  return (
    <Stack>
      <Button size="xl" fullWidth loading={start.isPending || going} onClick={() => start.mutate()}>
        Update
      </Button>
      {start.isError && !going && <Alert color="red">{start.error.message}</Alert>}
      {runs.isError && <Alert color="red">{runs.error.message}</Alert>}

      {runs.data?.length === 0 && <Text c="dimmed">No runs yet.</Text>}
      {run.data && (
        <Paper withBorder p="sm">
          <Stack gap="xs">
            <Status run={run.data} />
            {run.data.error && <Alert color="red">{run.data.error}</Alert>}
            {run.data.output && <Code block>{run.data.output}</Code>}
          </Stack>
        </Paper>
      )}

      {runs.data && runs.data.length > 1 && (
        <Stack gap={4}>
          <Text size="sm" c="dimmed">
            Earlier runs
          </Text>
          {runs.data.map((earlier) => (
            <UnstyledButton
              key={earlier.id}
              onClick={() => setPicked(earlier.id)}
              aria-current={earlier.id === shown}
              p={4}
            >
              <Status run={earlier} />
            </UnstyledButton>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

function Status({ run }: { run: RunSummary }) {
  return (
    <Group justify="space-between" wrap="nowrap">
      <Badge color={COLOUR[run.status]}>{run.status}</Badge>
      <Text size="sm" c="dimmed">
        {when(run.finished_at ?? run.started_at ?? run.created_at)}
      </Text>
    </Group>
  );
}
