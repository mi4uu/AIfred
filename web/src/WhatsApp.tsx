import { useEffect, useRef, useState } from "react";
import { Badge, Box, Button, Card, Flex, Heading, Text } from "@radix-ui/themes";
import { waStart, waStatus, type WaStatus } from "./api";

const COLORS: Record<string, "gray" | "amber" | "green" | "red"> = {
  idle: "gray",
  pairing: "amber",
  connected: "green",
  error: "red",
  unavailable: "gray",
};

export function WhatsApp() {
  const [st, setSt] = useState<WaStatus | null>(null);
  const timer = useRef<number | null>(null);

  async function refresh() {
    try {
      setSt(await waStatus());
    } catch {
      /* ignore transient */
    }
  }

  async function enable() {
    setSt(await waStart());
  }

  useEffect(() => {
    refresh();
    timer.current = window.setInterval(refresh, 2000); // poll for QR / connection
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const state = st?.state ?? "…";

  return (
    <Card mb="3">
      <Flex justify="between" align="center" mb="2">
        <Heading size="3">WhatsApp</Heading>
        <Badge color={COLORS[state] ?? "gray"}>{state}</Badge>
      </Flex>

      {st?.paired && state !== "connected" && (
        <Text size="2" color="gray">Paired — connecting…</Text>
      )}

      {!st?.paired && state !== "pairing" && (
        <Button size="2" onClick={enable} disabled={state === "unavailable"}>
          Enable WhatsApp
        </Button>
      )}

      {state === "pairing" && (
        <Box>
          <Text size="2" color="gray">Scan in WhatsApp → Linked devices:</Text>
          {st?.qr ? (
            <Box mt="2"><img src={st.qr} width={220} height={220} alt="WhatsApp QR" /></Box>
          ) : (
            <Text size="2" mt="2">Generating QR…</Text>
          )}
        </Box>
      )}

      {state === "error" && <Text size="2" color="red">{st?.error}</Text>}
    </Card>
  );
}
