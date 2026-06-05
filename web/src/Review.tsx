import { useEffect, useState } from "react";
import { Badge, Box, Button, Card, Flex, Heading, Text } from "@radix-ui/themes";
import { decideReview, getReview, type ReviewDecision, type ReviewItem } from "./api";

// "Do decyzji" — items the small model was unsure about. The owner's verdict
// becomes a learned rule (active learning), so next time it's handled silently.
export function Review() {
  const [items, setItems] = useState<ReviewItem[]>([]);

  async function refresh() {
    setItems(await getReview());
  }
  useEffect(() => {
    refresh();
  }, []);

  async function decide(id: number, decision: ReviewDecision) {
    setItems((x) => x.filter((i) => i.id !== id)); // optimistic
    await decideReview(id, decision);
  }

  if (items.length === 0) return null; // hide panel when empty

  return (
    <Card mb="3">
      <Heading size="3" mb="2">
        Do decyzji <Badge color="iris">{items.length}</Badge>
      </Heading>
      <Flex direction="column" gap="2">
        {items.map((it) => (
          <Box key={it.id} style={{ borderLeft: "2px solid var(--iris-7)", paddingLeft: 8 }}>
            <Text size="1" style={{ display: "block" }}>{it.text}</Text>
            <Text size="1" color="gray">AIfred sugeruje: {it.suggest === "high" ? "ważne" : it.suggest}</Text>
            <Flex gap="1" mt="1" wrap="wrap">
              <Button size="1" color="green" onClick={() => decide(it.id, "important")}>Ważne</Button>
              <Button size="1" variant="soft" onClick={() => decide(it.id, "not_important")}>Nieważne</Button>
              <Button size="1" variant="soft" color="gray" onClick={() => decide(it.id, "mute")}>Wycisz</Button>
            </Flex>
          </Box>
        ))}
      </Flex>
    </Card>
  );
}
