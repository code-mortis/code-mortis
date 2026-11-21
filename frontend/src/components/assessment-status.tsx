import type { Schema } from "../api";
import { Status, active } from "./common";
import { badgeRadius } from "./ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

type Assessment = {
  status: string; finding_id?: string | null; is_new?: boolean; assessment_only?: boolean;
  result?: { verdict?: string } | null; progress_phase?: string | null;
};

type SecurityOutcome = Schema["SecurityAssessmentOut"]["outcome"];
const securityResults: Partial<Record<SecurityOutcome, { value: string; label: string; description: string }>> = {
  uncertain: { value: "partial", label: "Uncertain", description: "The available evidence did not settle this proposal." },
  failed: { value: "failed", label: "Failed", description: "An error prevented the assessment from finishing." },
  interrupted: { value: "interrupted", label: "Interrupted", description: "The assessment stopped before reaching a result." },
  withheld: { value: "partial", label: "Withheld", description: "No validated finding was saved for this assessment." },
};

function feedback(candidate: Assessment) {
  if (candidate.assessment_only && candidate.result?.verdict === "confirmed") return { label: "Confirmed at original commit", description: "Confirmed by saved-commit evidence. No finding or comment was created after merge." };
  if (candidate.finding_id && candidate.is_new) return { label: "New validated finding", description: "Confirmed by evidence and saved as a new finding." };
  if (candidate.finding_id) return { label: "Repeated observation", description: "Matches an existing finding from review history." };
  if (candidate.result?.verdict === "rejected") return { label: "Not supported", description: "The evidence did not support this proposed issue." };
  if (candidate.status === "running" || candidate.progress_phase === "validating") return { label: "Assessing", description: "Checking evidence before accepting this proposed finding." };
  if (candidate.status === "queued") return { label: "Awaiting assessment", description: "Queued for assessment." };
  return { label: "Withheld", description: "Not accepted as a finding because assessment was uncertain or incomplete." };
}

export function AssessmentStatus({ candidate, inCard = false, securityOutcome }: {
  candidate: Assessment; inCard?: boolean; securityOutcome?: SecurityOutcome;
}) {
  const securityResult = securityOutcome ? securityResults[securityOutcome] : undefined;
  const detail = securityResult ?? feedback(candidate);
  let value = "partial";
  if (candidate.finding_id || (candidate.assessment_only && candidate.result?.verdict === "confirmed")) value = "completed";
  else if (candidate.result?.verdict === "rejected") value = "rejected";
  else if (active(candidate.status)) value = candidate.status;
  return <Tooltip>
    <TooltipTrigger asChild><span tabIndex={inCard ? undefined : 0} className={`inline-flex cursor-pointer ${badgeRadius} outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring`}>
      <Status value={securityResult?.value ?? value} label={detail.label} />
      {inCard && <span className="sr-only">{detail.description}</span>}
    </span></TooltipTrigger>
    <TooltipContent className="max-w-xs" sideOffset={6}>{detail.description}</TooltipContent>
  </Tooltip>;
}
