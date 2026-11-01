# Create a scenario by voice

In the simulation workspace, choose **New scenario → Start voice** and begin with “Creează un scenariu nou…” or “Create a new scenario…”, followed by the situation to simulate. For example:

> Creează un scenariu nou la Lisabona. Un avion cu 24 de pasageri vine de la Londra și este deja în aer. Furtuna nu permite aterizarea. Alege tu aeroportul de deviere și organizează transportul până la Lisabona.

The ordinary microphone accepts the same request. Opening the dialog or starting voice does not change the scene. No command is inserted into the transcript: the operator explicitly asks for a new scenario. **Write instead** focuses the existing command draft without replacing its contents.

An explicit new-scenario request means an empty reset followed by newly authored inventory, passengers and disruptions in one validated transaction. The user does not need a separate reset command. Before creating an underspecified scene, the assistant asks a concise question about the missing operational facts and offers to choose simulated values for optional details. Questions leave the current scene unchanged. Previously supplied answers remain in context; the user need not repeat the whole request. “Completează tu restul” explicitly delegates the missing demonstration values, which the assistant discloses. A sufficiently specified or explicitly delegated request proceeds without a redundant confirmation. Later requests to add or modify a service preserve the current scene.

For example, “Vreau un scenariu nou la Lisabona, cu un avion care nu poate ateriza din cauza furtunii” can lead to a question about passenger count, flight origin and their final destination. The reply “24 de pasageri din Londra, deja în aer, destinația finală Lisabona; completează tu restul” completes that pending scenario rather than modifying the old inventory.

Voice stays connected after creation. The verified receipt remains visible in the replacement scene even when the state update arrives before the HTTP response. Receipt ownership requires a matching simulation reset at the expected version; an unrelated scene switch or old receipt cannot replace the current review.

Live inventory uses its existing evidence-based workflow. Synthetic scene creation is exposed only in the simulation workspace.

Validation includes frontend lifecycle and HTTP/state-update ordering tests, backend reset and provider tests, and a real GPT-Live → Responses → Decisions check using generated Romanian speech in an isolated database. Synthetic speech verifies transport, interpretation and delegation; it does not test the physical microphone or room acoustics. See [spoken verification evidence](verification/new-scene-spoken-check.json).
