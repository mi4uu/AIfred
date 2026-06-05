import { useEffect, useState } from "react";
import { Badge, Box, Button, Card, Flex, Heading, IconButton, Select, Text, TextField } from "@radix-ui/themes";
import { TrashIcon } from "@radix-ui/react-icons";
import { addRule, deleteRule, listRules, type Rule } from "./api";

const SCOPES = ["sender", "group", "domain", "category"];
const ACTIONS = ["mute", "vip", "high", "medium", "low"];
const ACT_COLOR: Record<string, "red" | "green" | "amber" | "gray"> = {
  mute: "gray",
  vip: "green",
  high: "red",
  medium: "amber",
  low: "gray",
};

export function Rules() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [scope, setScope] = useState("sender");
  const [action, setAction] = useState("mute");
  const [pattern, setPattern] = useState("");

  async function refresh() {
    setRules(await listRules());
  }
  useEffect(() => {
    refresh();
  }, []);

  async function add() {
    if (!pattern.trim()) return;
    await addRule(scope, action, pattern.trim());
    setPattern("");
    refresh();
  }

  async function remove(id: number) {
    await deleteRule(id);
    setRules((x) => x.filter((r) => r.id !== id));
  }

  return (
    <Card mb="3">
      <Heading size="3" mb="2">Reguły ważności</Heading>
      <Flex direction="column" gap="1" mb="2">
        {rules.length === 0 && <Text size="1" color="gray">Brak reguł. Ucz przez czat lub dodaj poniżej.</Text>}
        {rules.map((r) => (
          <Flex key={r.id} align="center" gap="2">
            <Badge color={ACT_COLOR[r.action] || "gray"}>{r.action}</Badge>
            <Box flexGrow="1"><Text size="1">{r.scope}: {r.pattern}</Text></Box>
            <IconButton size="1" variant="ghost" color="red" onClick={() => remove(r.id)}>
              <TrashIcon />
            </IconButton>
          </Flex>
        ))}
      </Flex>
      <Flex gap="1" align="center" wrap="wrap">
        <Select.Root value={action} onValueChange={setAction} size="1">
          <Select.Trigger />
          <Select.Content>{ACTIONS.map((a) => <Select.Item key={a} value={a}>{a}</Select.Item>)}</Select.Content>
        </Select.Root>
        <Select.Root value={scope} onValueChange={setScope} size="1">
          <Select.Trigger />
          <Select.Content>{SCOPES.map((s) => <Select.Item key={s} value={s}>{s}</Select.Item>)}</Select.Content>
        </Select.Root>
        <Box flexGrow="1">
          <TextField.Root size="1" placeholder="np. Anna / netflix.com" value={pattern}
            onChange={(e) => setPattern(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        </Box>
        <Button size="1" onClick={add}>Dodaj</Button>
      </Flex>
    </Card>
  );
}
