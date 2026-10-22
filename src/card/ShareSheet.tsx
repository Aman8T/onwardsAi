/**
 * The share sheet as a dialog: the overlay the account screens use (a centred dialog, a bottom sheet under 760 px,
 * focus trapped, Escape closes) around `ShareBody`. Loaded on demand by `ShareButton`.
 */
import { useId } from 'react';
import { NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { Sheet } from '../auth/Sheet';
import { realClient } from './api';
import type { CardClient } from './api';
import { ShareBody } from './ShareBody';

export interface ShareSheetProps {
  open: boolean;
  onClose: () => void;
  profileId: string;
  client?: CardClient;
  onAnalyse: () => void;
  onSignIn?: () => void;
}

export function ShareSheet({ open, onClose, profileId, client = realClient, onAnalyse, onSignIn }: ShareSheetProps) {
  const titleId = useId();
  const narrow = useMediaQuery(NARROW_QUERY);
  return (
    <Sheet open={open} onClose={onClose} labelledBy={titleId} className="cs-sheet">
      <ShareBody
        profileId={profileId}
        client={client}
        narrow={narrow}
        titleId={titleId}
        onClose={onClose}
        onAnalyse={onAnalyse}
        onSignIn={onSignIn}
      />
    </Sheet>
  );
}
