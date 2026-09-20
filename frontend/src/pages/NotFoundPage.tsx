import { Link } from "react-router";
import { EmptyState } from "../components/ui/Misc";
import { Button } from "../components/ui/Button";

export function NotFoundPage() {
  return <EmptyState title="Page not found" action={<Link to="/creators"><Button>Go to creators</Button></Link>} />;
}
