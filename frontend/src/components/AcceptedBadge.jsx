import { Badge } from "./ui.jsx";
import { Icon } from "./icons.jsx";

/**
 * "✓ Accepted" — a route somebody has taken up.
 *
 * It stands where the Accept button was, because a control that has been
 * spent should read as a finished state rather than as a button that stopped
 * working. `Badge` is the application's one status treatment (the design
 * vocabulary in `ui.jsx`), so this is that badge with the tick on it and not
 * a fifth spelling of a pill.
 *
 * `success` is the reader's own claim; `neutral` is a colleague's, because a
 * green tick against somebody else's name reads as "your action worked".
 */
export default function AcceptedBadge({ by = null, tone = "success" }) {
  return (
    <Badge tone={tone}>
      <Icon name="check" className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
      Accepted{by ? ` · ${by}` : ""}
    </Badge>
  );
}
