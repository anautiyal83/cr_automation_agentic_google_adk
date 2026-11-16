# Azalio Autonomous Network Platform — what it is, in plain words

Who this is for: anyone new to the product (developers, testers, managers). The framework details behind it
are in [specs/000-product-context](../specs/000-product-context/spec.md).

## The problem: changes on network nodes

An operator's network runs on many machines (nodes), such as **PGW**, **RDS**, **EIR** and **MRF**. They need
routine changes all the time, for example:

- changing a subscriber's capabilities (PGW use case);
- loading a new list of phone models, the TAC file (EIR);
- loading new voice announcements (MRF).

Each change comes in as a **Change Request (CR)**: an order to make that change on certain nodes.

## How a change is done manually

An engineer gets the CR and two documents:

- the **MOP** (Method of Procedure): step-by-step instructions, "log in here, run this, check you see that";
- the **CIQ**: an Excel sheet with the actual values (node names, IPs, subscriber numbers, file names).

They then follow the MOP by hand, usually in a night maintenance window:

1. **Log in**: SSH to a jump server (NIAM), pick the node, become root.
2. **Health check before**: run status commands and check by eye that the node is healthy.
3. **Backup**: copy the files about to be changed to a safe place (the repo server).
4. **Make the change**: copy files over, run the load or modify commands, typing the values from the Excel.
5. **Verify**: run read commands and compare the result with what the CIQ asked for.
6. **Health check after**: is the node still healthy?
7. **If anything went wrong**: restore the backup by hand.
8. **Record**: save the session logs and report back.

This is slow, easy to get wrong (a typo, a skipped step, an unnoticed error), hard to repeat across 50 nodes,
and depends on the engineer's experience.

## The CR automation framework

The **CR automation framework** (cli-automation, `lib/cli-automation-standalone.jar`; production runs a JAR with
the same code under another name) is a robot that carries out the change. You give it:

- a **recipe**: the workflow YAML, saying which machines to log in to, which commands to type in what order,
  what a good result looks like, and what to do if something goes wrong;
- the **values**: the CIQ, turned into CIQ JSON, saying which nodes and which subscribers or files.

It then logs in, types every command, checks every answer, takes a backup, undoes the change if something
fails, and writes a report. It does exactly what the engineer would, only faster, the same way every time,
and on many nodes at once.

## Where our product fits

The framework needs a **recipe for each kind of change** (each use case, UC). Without our product a developer
writes that YAML by hand from the MOP, which takes skill and time.

**Our product writes the recipe automatically.** It reads the same MOP, CIQ and session logs the engineer would
use, turns them into the YAML and its helper scripts, and tests the result on a simulated network before it is
ever used on a real one:

| Steps | What happens |
|-------|--------------|
| 1–2 | Read the documents, ask the user what is missing, write the requirements |
| 3–5 | Build and test the CIQ validation rules and the CIQ JSON |
| 6–7 | Build the workflow YAML and helper scripts, check them against the MOP |
| 8–9 | Build a simulator of the nodes (MockWire) and run the real framework JAR against it |
| 10 | Write the report and an export package that runs outside the platform |

### An analogy

- **Manual**: a chef cooks from a handwritten recipe card.
- **The framework**: a kitchen robot that cooks perfectly, but only from recipes written in its own exact format.
- **Our product**: reads the handwritten card, writes it in the robot's format, and test-cooks it in a practice
  kitchen first.

## Words used across the product

| Word | Meaning |
|------|---------|
| CR | Change Request: an order to make one change on certain nodes |
| UC | Use case: one kind of change (e.g. `PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION`); one recipe per UC |
| MOP | Method of Procedure: the engineer's step-by-step instructions |
| CIQ | The Excel sheet with the values for one change; the CIQ JSON is the framework's copy of it |
| Session logs | Recorded terminal sessions of the change done by hand; they show what each command really prints |
| NIAM | The jump server every Nokia node is reached through (log in, then choose the node by its neId) |
| Repo server | The server backups are copied to, and files to load are copied from |
| Workflow YAML | The recipe the framework runs: nodes, phases (health check, backup, activity, rollback), steps |
| MockWire | The simulator: pretends to be the nodes so a recipe can be tested safely |
| Vendor profile | What the platform knows per vendor and node type (logins, prompts, CIQ column meanings) |

This describes the process as our code and the documents we have received (MOPs, CIQs, production workflows)
show it; correct it here if operations works differently in practice.
