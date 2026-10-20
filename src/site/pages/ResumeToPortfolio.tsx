import type { ContentPage } from '../types';

export const resumeToPortfolio: ContentPage = {
  path: '/resume-to-portfolio',
  title: 'Resume to portfolio page, built from cited facts',
  description:
    'Turn a resume into a shareable portfolio page where every statement traces to a line of your sources. Pick a look, edit any text and decide what is public.',
  h1: 'Turn your resume into a portfolio page',
  lede: 'A portfolio page should show what you did, not what a template invented. Leafout builds yours from facts copied from your resume and links, writes the copy only from those facts, and lets you decide what is public.',
  blurb: 'A shareable page whose every line traces back to your sources.',
  cta: { label: 'Build my portfolio', href: '/' },
  related: ['/skill-graph', '/how-it-works', '/about'],
  faq: [
    {
      q: 'Will it make up achievements?',
      a: 'No. Every sentence must cite facts that are copied from your sources. A sentence the facts do not support is replaced by the fact’s own wording.',
    },
    {
      q: 'Are my email, phone number and grades shown?',
      a: 'Only if you opt in. Lines with contact details, addresses or a date of birth are refused when the facts are read, and the public page leaves out email, phone and grades unless you choose to show them.',
    },
    {
      q: 'Can I edit the page?',
      a: 'Yes. You can choose among the looks Leafout composes, edit any text, hide a domain and decide whether the page is public. Text you write yourself is plain text and is marked as yours.',
    },
    {
      q: 'Will search engines list my page?',
      a: 'Share links ask search engines not to list them, so people reach your page because you sent them the link.',
    },
    {
      q: 'Does it cost anything?',
      a: 'A public portfolio page is part of the free plan. A portfolio on your own domain is planned and is not built yet.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>How the page is built</h2>
        <p>
          First, a model reads each of your sources with the lines numbered and cites the line numbers of what it finds. Code then copies those lines
          word for word and keeps a fact only when its title and employer really appear in the cited lines. The model types only short fields such
          as a title or an employer, never the evidence itself.
        </p>
        <p>
          Second, code composes the design. Third, a model writes the copy into fixed slots. Fourth, the copy is checked against the facts. The page
          first appears with each fact&apos;s own wording and is then upgraded with the checked copy.
        </p>
      </section>

      <section>
        <h2>Looks without surprises</h2>
        <p>
          The design comes from a closed kit that was tested in advance: eight colour families, three shapes for the background, four pairings of type,
          a choice of corner shapes and spacing, and blocks such as a hero, proof, about, experience, projects and contact. Code scores the valid
          combinations against the shape of your career and your skills, breaks ties in a way that is stable for you, and keeps the top three looks
          with the reasons it chose them.
        </p>
        <p>
          A page can never come out broken, because every combination is valid. A block that has too little to show is left out of the public page
          rather than padded. An alternative skin, Old Newspaper, shows the same content as a printed front page.
        </p>
      </section>

      <section>
        <h2>Copy that has to cite</h2>
        <p>
          A model writes each paragraph into a slot with a character limit and must name the facts it rests on. Then every sentence is checked:
          the numbers with units, the names and the dates must appear in the quotes of the cited facts, and promotional words or seniority verbs the
          facts do not support are refused. A slot that fails once is asked again, and then it falls back to the fact&apos;s own wording.
        </p>
        <p>
          Numbers pass through untouched. A script compares every number drawn in the charts with its source, in every look, so a chart cannot say
          something your facts do not.
        </p>
      </section>

      <section>
        <h2>What is public</h2>
        <p>
          The public page shows only the blocks you have made visible. It carries no profile id and no raw evidence lines, and it leaves out skills
          scored below 45. Email, phone and grades appear only if you opt in, and personal details are scrubbed from the text.
        </p>
        <p>
          The same evidence that fills the page also feeds the radar of your <a href="/skill-graph">skill graph</a>, and the chart on the page is
          chosen by the shape of your skills. A radar card you can place in a GitHub or LinkedIn profile is planned and is not built yet.
        </p>
      </section>

      <section>
        <h2>Who it is for</h2>
        <p>
          The page works for any profession. A nurse, an accountant and an engineer start from the same thing, a resume, and Leafout adds GitHub,
          apps and links when they exist. If your only public proof so far is the resume, the page shows that and no more.
        </p>
      </section>
    </>
  ),
};
