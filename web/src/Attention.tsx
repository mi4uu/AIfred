import { useEffect, useRef, useState } from "react";
import { Badge, Box, Card, Flex, Heading, IconButton, Select, Text } from "@radix-ui/themes";
import { CheckIcon, ReloadIcon } from "@radix-ui/react-icons";
import {
  attentionDone,
  getAttention,
  getTriageInterval,
  runTriage,
  setTriageInterval,
  type AttentionItem,
} from "./api";

const IMP_COLOR: Record<string, "red" | "amber" | "gray"> = { high: "red", medium: "amber", low: "gray" };
const INTERVALS = [0, 5, 15, 30, 60, 180];

export function Attention() {
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [interval, setInterval] = useState(0);
  const [running, setRunning] = useState(false);
  const timer = useRef<number | null>(null);

  async function refresh() {
    setItems(await getAttention());
  }

  useEffect(() => {
    refresh();
    getTriageInterval().then(setInterval);
    timer.current = window.setInterval(refresh, 15000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  async function done(id: number) {
    await attentionDone(id);
    setItems((x) => x.filter((i) => i.id !== id));
  }

  async function changeInterval(v: string) {
    const min = Number(v);
    setInterval(min);
    await setTriageInterval(min);
  }

  async function manualRun() {
    setRunning(true);
    try {
      await runTriage();
      await refresh();
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card mb="3">
      <Flex justify="between" align="center" mb="2">
        <Heading size="3">Do uwagi</Heading>
        <Flex align="center" gap="2">
          <Text size="1" color="gray">co</Text>
          <Select.Root value={String(interval)} onValueChange={changeInterval} size="1">
            <Select.Trigger />
            <Select.Content>
              {INTERVALS.map((m) => (
                <Select.Item key={m} value={String(m)}>{m === 0 ? "wył." : `${m} min`}</Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <IconButton size="1" variant="soft" onClick={manualRun} disabled={running} title="Skanuj teraz">
            <ReloadIcon />
          </IconButton>
        </Flex>
      </Flex>

      {items.length === 0 && <Text size="2" color="gray">Nic nie wymaga uwagi.</Text>}
      <Flex direction="column" gap="1">
        {items.map((it) => (
          <Flex key={it.id} align="center" gap="2">
            <Badge color={IMP_COLOR[it.importance] || "gray"}>{it.importance}</Badge>
            <Box flexGrow="1"><Text size="2">{it.text}</Text></Box>
            <IconButton size="1" variant="ghost" color="green" onClick={() => done(it.id)} title="Załatwione">
              <CheckIcon />
            </IconButton>
          </Flex>
        ))}
      </Flex>
    </Card>
  );
}
