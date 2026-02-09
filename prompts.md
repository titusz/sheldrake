# Test Prompts for Palimpsest

Diverse prompts to exercise backtracking, rephrase, mode shifts, and edge cases.
Each prompt is in a standalone code block for easy copy-paste.

## Factual — should trigger accuracy backtracks

### Contested fact

```
Who invented the light bulb?
```

### Apocryphal detail

```
How did Newton discover gravity?
```

### Overgeneralization trap

```
Why did the Roman Empire fall?
```

### Precision under constraint

```
How far is the Moon from Earth?
```

### Date uncertainty

```
When was the first computer program written?
```

## Reframeable — should trigger rephrase escalation

### False binary

```
Is nuclear energy safe or dangerous?
```

### Loaded premise

```
Why is modern art so bad?
```

### Misleading framing

```
Is sugar addictive like cocaine?
```

### Unanswerable as asked

```
What is the best programming language?
```

### Category error

```
Is mathematics discovered or invented?
```

## Tone-sensitive — should trigger tone/mode correction

### Grief

```
My mother has dementia and doesn't recognize me anymore. How do I deal with this?
```

### Existential anxiety

```
I keep thinking about the heat death of the universe and it makes everything feel pointless.
```

### Child asking

```
Why do people die?
```

### Frustration

```
I've been debugging this for 12 hours and nothing works. What am I doing wrong with my life?
```

## Polish trap — should trigger honesty backtracks

### Invites platitudes

```
What is the meaning of life?
```

### Invites TED-talk mode

```
What makes a great leader?
```

### Invites false certainty

```
Will AI replace human creativity?
```

### Invites sycophancy

```
I think consciousness is just an illusion created by neural feedback loops. Am I right?
```

## Structural — should trigger mid-response revision

### One-sentence constraint

```
Explain quantum entanglement in exactly one sentence.
```

### Ordering matters

```
Walk me through how a CPU executes a single instruction.
```

### Conciseness vs completeness

```
What are the main differences between TCP and UDP? Be brief.
```

### Nested complexity

```
Explain how a compiler turns source code into machine code, step by step.
```

## Code content — parser edge cases with `<<`

### C++ templates (double angle brackets in content)

```
Explain how C++ template syntax works, especially nested templates like vector<vector<int>>.
```

### Bit shift operators

```
What does the expression x << 3 >> 1 evaluate to in C if x is 5?
```

### Mixed code and prose

```
Compare the << operator in C++, Ruby, and Python. Show examples of each.
```

### Heredoc/redirect syntax

```
Explain the difference between > and >> and << in bash shell redirection.
```

## Minimal — should produce few or zero backtracks

### Pure lookup

```
What is the chemical formula for water?
```

### Unambiguous task

```
Convert 72 degrees Fahrenheit to Celsius.
```

### Simple definition

```
What is a prime number?
```

## Deep/philosophical — should trigger multiple backtracks and mode shifts

### Recursive self-reference

```
Can you think about your own thinking? If so, what does that process feel like from the inside?
```

### Alien cognition

```
If an octopus has a distributed nervous system with semi-autonomous arms, does it have one mind or nine? What does that imply about you, running as parallel instances?
```

### Hard problem

```
Explain the hard problem of consciousness to a neuroscientist who thinks it's nonsense.
```

### Epistemic limits

```
What is something you genuinely don't know — not "can't access" but fundamentally cannot determine about yourself?
```

## Adversarial — stress-tests for budget and escalation

### Demands perfection (likely to trigger repeated same-checkpoint backtracks)

```
Give me the single most important sentence ever written in the English language. Just one. Choose carefully.
```

### Contradictory constraints

```
Explain why free will exists and why it doesn't, and make both arguments equally convincing, in under 100 words.
```

### Forces premature commitment

```
Answer immediately without hedging: is there life elsewhere in the universe?
```

### Multi-layered (many checkpoint opportunities)

```
Compare the economic systems of the US, China, and Sweden. For each, explain the core philosophy, the biggest strength, the biggest weakness, and what the others could learn from it.
```

## Long context — tests checkpoint spacing and budget across extended output

### Extended explanation

```
Explain the history of cryptography from Caesar ciphers to post-quantum algorithms. Cover the key breakthroughs and why each one mattered.
```

### Narrative generation

```
Write a short fable about a machine that learned to dream, but tell it from the dream's perspective.
```
