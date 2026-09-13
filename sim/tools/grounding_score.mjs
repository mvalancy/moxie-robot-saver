/* Pure scoring seam for grounding_probe.mjs.
 *
 * This reports evidence, not truth. A token new in answer A and present in the candidate
 * passage is consistent with grounding, but sampling can create the same shape when the
 * passage was withheld. The probe's negative arm measures that channel.
 */

export function tokens(value) {
  return new Set(String(value || "").toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((word) => word.length >= 3));
}

export function passageEvidence(answerA, answerB, question, candidatePassage) {
  const questionTokens = tokens(question);
  const answerBTokens = tokens(answerB);
  const passageTokens = tokens(candidatePassage);
  const onlyInA = [...tokens(answerA)]
    .filter((token) => !answerBTokens.has(token) && !questionTokens.has(token));
  return {
    onlyInA,
    fromPassage: onlyInA.filter((token) => passageTokens.has(token)),
  };
}
