/**
 * The report's owner sheet as a dialog: the overlay the account screens use (a centred dialog, a bottom sheet under
 * 760 px, focus trapped, Escape closes) around `ReportBody`. Loaded on demand by `ReportButton`.
 */
import { useId } from 'react';
import { Sheet } from '../auth/Sheet';
import { NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { realClient } from './api';
import type { ReportClient } from './api';
import { ReportBody } from './ReportBody';

export interface ReportSheetProps {
  open: boolean;
  onClose: () => void;
  profileId: string;
  client?: ReportClient;
  onAnalyse: () => void;
  onSignIn?: () => void;
  onPrepare: (roleId: string) => void;
  onResume: () => void;
}

export function ReportSheet({ open, onClose, profileId, client = realClient, onAnalyse, onSignIn, onPrepare, onResume }: ReportSheetProps) {
  const titleId = useId();
  const narrow = useMediaQuery(NARROW_QUERY);
  return (
    <Sheet open={open} onClose={onClose} labelledBy={titleId} className="rp-sheet">
      <ReportBody
        profileId={profileId}
        client={client}
        narrow={narrow}
        titleId={titleId}
        onClose={onClose}
        onAnalyse={onAnalyse}
        onSignIn={onSignIn}
        onPrepare={onPrepare}
        onResume={onResume}
      />
    </Sheet>
  );
}
