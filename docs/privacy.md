# Privacy

**Status: draft.** The live demo does not exist yet. This note describes how it is designed to handle data, and it will be updated when the demo ships.

- **No audio is stored.** Your microphone audio streams to the speech-to-text provider, and the agent's voice streams back from the text-to-speech provider. This project saves neither.
- **Transcripts are held in memory only,** for the length of the call, and are discarded when the call ends.
- **Only anonymous aggregate metrics are logged:** timings, counts of tool calls and checks, and cost. No audio, transcripts or names are logged.
- **Rate limits are planned** to use a salted hash of your IP address, held in memory and reset daily.
- **A notice appears before the microphone opens.** You can type instead of speaking.
- **Third parties** process the call content under their own terms: Sarvam AI for speech-to-text and text-to-speech, and the language-model provider. The plan is to set the demo's provider accounts to opt out of training and to keep data for the shortest time each provider allows.
- **All order data is synthetic.** Please don't share real personal details in the demo.

Questions: open an issue on this repository.
