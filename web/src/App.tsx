import { useEffect, useRef, useState } from "react";
import {
  Avatar,
  Badge,
  Box,
  Button,
  Callout,
  Card,
  Flex,
  Heading,
  IconButton,
  ScrollArea,
  Spinner,
  Text,
  TextField,
} from "@radix-ui/themes";
import { PlusIcon, TrashIcon } from "@radix-ui/react-icons";
import { Attention } from "./Attention";
import { Review } from "./Review";
import { Rules } from "./Rules";
import {
  confirm,
  deleteSession,
  listSessions,
  newSession,
  sessionMessages,
  streamChat,
  type ChatMessage,
  type Pending,
  type Session,
} from "./api";
import { WhatsApp } from "./WhatsApp";

// friendly PL labels for the activity indicator
const TOOL_LABEL: Record<string, string> = {
  brain_context: "czyta brain.md",
  brain_now: "sprawdza datę",
  journal_add: "zapisuje dziennik",
  journal_recall: "przeszukuje dziennik",
  journal_distill: "podsumowuje notatki",
  task_add: "dodaje zadanie",
  task_list: "czyta zadania",
  task_done: "zamyka zadanie",
  gmail_search: "szuka w mailu",
  gmail_get: "czyta wiadomość",
  calendar_list: "czyta kalendarz",
  calendar_create: "tworzy wydarzenie",
  calendar_update: "zmienia wydarzenie",
  calendar_propose: "planuje z brain.md",
  daily_digest: "robi podsumowanie",
  whatsapp_recent: "czyta WhatsApp",
  whatsapp_chats: "sprawdza czaty WhatsApp",
  people_lookup: "sprawdza kontakt",
};

function statusLabel(event: string, detail: string): string {
  if (event === "tool") return `${TOOL_LABEL[detail] || detail}…`;
  if (event === "thinking") return "AIfred myśli…";
  return "…";
}

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function refreshSessions() {
    setSessions(await listSessions());
  }

  useEffect(() => {
    refreshSessions();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 1e9, behavior: "smooth" });
  }, [msgs, activity]);

  async function openSession(id: number) {
    setActiveId(id);
    setMsgs(await sessionMessages(id));
    setPending([]);
  }

  async function startNew() {
    const s = await newSession();
    await refreshSessions();
    setActiveId(s.id);
    setMsgs([]);
    setPending([]);
  }

  async function removeSession(id: number, e: React.MouseEvent) {
    e.stopPropagation();
    await deleteSession(id);
    if (activeId === id) {
      setActiveId(null);
      setMsgs([]);
    }
    refreshSessions();
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setInput("");
    setActivity("AIfred myśli…");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    try {
      await streamChat(text, activeId, (e) => {
        if (e.type === "session") setActiveId(e.session_id);
        else if (e.type === "status") setActivity(statusLabel(e.event, e.detail));
        else if (e.type === "reply") {
          setMsgs((m) => [...m, { role: "assistant", content: e.reply }]);
          setPending(e.pending || []);
          setActivity(null);
        } else if (e.type === "error") {
          setMsgs((m) => [...m, { role: "assistant", content: `błąd: ${e.error}` }]);
          setActivity(null);
        }
      });
      refreshSessions(); // pick up title/order change
    } catch (err) {
      setMsgs((m) => [...m, { role: "assistant", content: `błąd połączenia: ${String(err)}` }]);
    } finally {
      setActivity(null);
      setBusy(false);
    }
  }

  async function resolve(token: string, approve: boolean) {
    await confirm(token, approve);
    setPending((p) => p.filter((x) => x.token !== token));
  }

  return (
    <Flex style={{ height: "100vh" }}>
      {/* sidebar */}
      <Box style={{ width: 260, borderRight: "1px solid var(--gray-a5)", padding: 12, overflowY: "auto" }}>
        <Flex justify="between" align="center" mb="3">
          <Flex align="center" gap="2">
            <Avatar size="2" radius="full" src="/alfred.png" fallback="🎩" />
            <Heading size="4">AIfred</Heading>
          </Flex>
          <IconButton size="1" onClick={startNew} title="Nowa sesja">
            <PlusIcon />
          </IconButton>
        </Flex>
        <Button variant="soft" size="2" style={{ width: "100%" }} mb="3" onClick={startNew}>
          <PlusIcon /> Nowa rozmowa
        </Button>
        <Flex direction="column" gap="1">
          {sessions.map((s) => (
            <Card
              key={s.id}
              variant={s.id === activeId ? "classic" : "surface"}
              style={{ cursor: "pointer", padding: 8 }}
              onClick={() => openSession(s.id)}
            >
              <Flex justify="between" align="center" gap="2">
                <Text size="2" truncate style={{ flexGrow: 1 }}>{s.title}</Text>
                <IconButton size="1" variant="ghost" color="red" onClick={(e) => removeSession(s.id, e)}>
                  <TrashIcon />
                </IconButton>
              </Flex>
            </Card>
          ))}
        </Flex>
        <Box mt="4">
          <Review />
          <Attention />
          <Rules />
          <WhatsApp />
        </Box>
      </Box>

      {/* chat */}
      <Flex direction="column" style={{ flexGrow: 1, padding: 16 }}>
        {pending.map((p) => (
          <Callout.Root color="amber" mb="3" key={p.token}>
            <Callout.Text>
              Potwierdzić <b>{p.tool}</b>? {JSON.stringify(p.args)}
            </Callout.Text>
            <Flex gap="2" mt="2">
              <Button size="1" onClick={() => resolve(p.token, true)}>Zatwierdź</Button>
              <Button size="1" variant="soft" color="red" onClick={() => resolve(p.token, false)}>Odrzuć</Button>
            </Flex>
          </Callout.Root>
        ))}

        <Card style={{ flexGrow: 1, marginBottom: 12, overflow: "hidden" }}>
          <ScrollArea ref={scrollRef as never} style={{ height: "100%" }} type="auto">
            <Flex direction="column" gap="2" p="2">
              {msgs.length === 0 && (
                <Text size="2" color="gray">Zacznij rozmowę z AIfred.</Text>
              )}
              {msgs.map((m, i) => (
                <Box key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "82%" }}>
                  <Card variant={m.role === "user" ? "classic" : "surface"}>
                    <Text size="2" style={{ whiteSpace: "pre-wrap" }}>{m.content}</Text>
                  </Card>
                </Box>
              ))}
              {activity && (
                <Flex align="center" gap="2" style={{ alignSelf: "flex-start" }}>
                  <Spinner size="1" />
                  <Badge color="iris" variant="soft">{activity}</Badge>
                </Flex>
              )}
            </Flex>
          </ScrollArea>
        </Card>

        <Flex gap="2">
          <Box flexGrow="1">
            <TextField.Root
              placeholder="Napisz do AIfred…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
            />
          </Box>
          <Button onClick={send} disabled={busy}>{busy ? <Spinner /> : "Wyślij"}</Button>
        </Flex>
      </Flex>
    </Flex>
  );
}
