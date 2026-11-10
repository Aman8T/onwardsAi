/**
 * The application inbox on fixtures (InboxPanel.tsx): the dev gallery's inbox screens and the panel's tests. One email
 * per status the person can see, measured against the gallery's `NOW`. Never a body and never a code, like the server.
 */
import type { InboxEmail, InboxStatus } from '../../../shared/inbox';
import { NOW } from './fixtures';

const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export const INBOX_OFF: InboxStatus = { enabled: false };

export const INBOX_NO_ADDRESS: InboxStatus = { enabled: true };

export const INBOX_ADDRESS: InboxStatus = {
  enabled: true,
  address: { email: 'jordan.k7m4q@apply.leafout.in', active: true, copies: true, createdAt: ago(9 * DAY) },
};

export const INBOX_ADDRESS_OFF: InboxStatus = {
  enabled: true,
  address: { email: 'jordan.k7m4q@apply.leafout.in', active: false, copies: true, createdAt: ago(9 * DAY) },
};

export const INBOX_EMAILS: InboxEmail[] = [
  {
    id: '41',
    receivedAt: ago(4 * MIN),
    sender: 'no-reply@greenhouse.io',
    senderDomain: 'greenhouse.io',
    subject: 'Your Greenhouse security code',
    status: 'verification',
    copyStatus: 'skipped',
  },
  {
    id: '40',
    receivedAt: ago(3 * HOUR),
    sender: 'talent@ostrava.example',
    senderDomain: 'ostrava.example',
    subject: 'Next steps for the Backend Engineer role',
    status: 'processed',
    stage: 'interviewing',
    applicationId: 'a_ostrava',
    quote: 'We would love to set up a 30-minute call with the hiring manager.',
    copyStatus: 'sent',
  },
  {
    id: '39',
    receivedAt: ago(1 * DAY),
    sender: 'jobs@heron.example',
    senderDomain: 'heron.example',
    subject: 'Thank you for applying',
    status: 'unmatched',
    quote: 'We have received your application.',
    copyStatus: 'sent',
  },
  {
    id: '38',
    receivedAt: ago(2 * DAY),
    sender: 'careers@marigold.example',
    senderDomain: 'marigold.example',
    subject: 'An update on your application',
    status: 'processed',
    stage: 'rejected',
    applicationId: 'a_marigold',
    quote: 'We have decided to move forward with other candidates.',
    copyStatus: 'failed',
  },
  {
    id: '37',
    receivedAt: ago(3 * DAY),
    sender: 'news@plover.example',
    senderDomain: 'plover.example',
    subject: 'Our newsletter',
    status: 'processed',
    stage: 'none',
    copyStatus: 'sent',
  },
  {
    id: '36',
    receivedAt: ago(4 * DAY),
    sender: 'hr@wren.example',
    senderDomain: 'wren.example',
    subject: 'Interview availability',
    status: 'ignored',
  },
  {
    id: '35',
    receivedAt: ago(5 * DAY),
    sender: 'ats@finch.example',
    senderDomain: 'finch.example',
    subject: '',
    status: 'failed',
    copyStatus: 'skipped',
  },
];
