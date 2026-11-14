# Agent trials (phase 4)

The release gate for v0.1 is that an agent given only `videocli skill install`, a shell and a
lecture file completes each task below with a verified result and without a human correcting
its commands. Record every wrong command, misread output and unnecessary step; fix the tool
first (better `next` hints, clearer errors), the skill text second. Keep transcripts under
`eval/trials/` as regression cases.

| # | Prompt | Done when |
|---|---|---|
| 1 | "Remove the dead air from `lecture.mp4` and give me a clean MP4." | `verify` passes; removed share reported to the user |
| 2 | "Cut the filler words and long pauses from this talk, and add captions." | `verify` passes; `.srt` written; no clipped words |
| 3 | "Find where I talk about binary search and cut everything before it." | `find` used for the time; `edl cut`; `verify` passes |
| 4 | "Make a 480p preview of the cleaned lecture so I can check the joins." | `render --preview` or `--around-cuts`; joins sheet offered |
| 5 | "The cut at 12:40 sounds wrong; fix it." | the agent inspects with `strip`/`sheet --cuts`, changes the EDL with commands, re-renders, re-verifies |

Status: not yet run. Results and the transcripts will be recorded here.
