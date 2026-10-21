import { Dialog } from 'radix-ui'
import { Button } from '@/components/ui/button'

const REPO_URL = 'https://github.com/farajgrow/Neural-Network-graph-for-scam-detection'

/** The process, in order, with who owned each part. */
const STEPS: { title: string; who: 'Arya' | 'Faraj' | 'Both'; text: string }[] = [
  {
    title: 'Agree a shared vocabulary',
    who: 'Both',
    text: 'A fixed list of 15 steps a scam can go through (first contact, moving platforms, fake proof, small payment…), plus 6 scam types. Every story and every match uses the same list.',
  },
  {
    title: 'Collect real stories',
    who: 'Arya',
    text: 'Scraped posts from r/Scams through a Reddit archive, keeping only posts long enough to tell a story.',
  },
  {
    title: 'Clean and anonymise',
    who: 'Arya',
    text: 'Removed duplicates, links and markdown, and replaced emails, phone numbers, wallets and usernames with placeholders.',
  },
  {
    title: 'Turn each story into steps',
    who: 'Arya',
    text: 'Each post became a timeline of steps from the shared list, with the scam type, the outcome and any amount lost. 487 stories made it through.',
  },
  {
    title: 'Check the labels by hand',
    who: 'Arya',
    text: 'A hand spot check of 20 random stories found 96.2% of fields correct.',
  },
  {
    title: 'Map the scripts',
    who: 'Faraj',
    text: 'Built a graph per scam type: how often each step happens, which step follows which, and how often people at each step lost money.',
  },
  {
    title: 'Find pinch and escape points',
    who: 'Faraj',
    text: 'Pinch points are steps where the loss rate jumps compared with the steps before. Escape points are steps most often followed by the victim getting out. Small samples are filtered out so they can’t win.',
  },
  {
    title: 'Find script variants',
    who: 'Faraj',
    text: 'Clustered the step sequences by how similar they are, which showed the same scam often runs in different ways, and that scripts cut across the scam-type labels.',
  },
  {
    title: 'Build the matcher',
    who: 'Both',
    text: 'Arya built the matcher: Jev reads a pasted message and answers a yes/no question for each step, then code turns that into a result. Faraj wired it into the API and this card, adding the loss rate from the real stories.',
  },
  {
    title: 'Train our own model',
    who: 'Faraj',
    text: 'A small neural network (GRU) trained on the stories to predict the scammer’s next move and the chance of losing money. Tested fairly against simple counting rules, it currently ties them. More stories should change that.',
  },
  {
    title: 'Design and ship the site',
    who: 'Faraj',
    text: 'Prototyped this retro look in Claude Design, then built it with React, React Flow and Tailwind on a FastAPI backend.',
  },
]

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Pulse the button to draw the eye to it. */
  hint?: boolean
}

export function AboutDialog({ open, onOpenChange, hint = false }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <span className="about-anchor">
        <Dialog.Trigger asChild>
          <Button variant="secondary" size="sm" className={hint ? 'about-trigger about-trigger--pulse' : 'about-trigger'}>
            [ how we built it ]
          </Button>
        </Dialog.Trigger>
      </span>
      <Dialog.Portal>
        <Dialog.Overlay className="about-overlay" />
        <Dialog.Content className="window about">
          <div className="window__bar">
            <Dialog.Close className="window__close about__close" aria-label="Close" />
            <Dialog.Title className="window__title">about_this_project</Dialog.Title>
          </div>
          <div className="about__body">
            <Dialog.Description className="about__lede">
              <strong>Scams follow scripts.</strong> We turned 487 real victim stories into step-by-step timelines, mapped
              each scam type as a graph, and found where people get hooked and where they get out. Paste a suspicious
              message and the site tells you which script you’re in, where you are in it, and how often people who took the
              next step lost money.
            </Dialog.Description>

            <section>
              <h3 className="about__h">Who built it</h3>
              <ul className="about__who">
                <li>
                  <strong>Arya</strong> · data: collecting, cleaning and anonymising the stories, turning them into steps,
                  the accuracy check, and the Jev matcher.
                </li>
                <li>
                  <strong>Faraj</strong> · analysis and app: the script graphs, pinch and escape points, clustering, our
                  neural network, the API and this site.
                </li>
              </ul>
              <p className="muted small">Started as a two-hour hackathon build, then extended.</p>
            </section>

            <section>
              <h3 className="about__h">How we built it</h3>
              <ol className="about__steps">
                {STEPS.map((s) => (
                  <li key={s.title}>
                    <div className="about__step-head">
                      <strong>{s.title}</strong>
                      <span className={`about__who-tag about__who-tag--${s.who.toLowerCase()}`}>{s.who}</span>
                    </div>
                    <p>{s.text}</p>
                  </li>
                ))}
              </ol>
            </section>

            <section>
              <h3 className="about__h">What we found</h3>
              <ul className="about__list">
                <li>
                  Once victims make even a <strong>small payment</strong>, the loss rate goes from 37% to{' '}
                  <strong className="about__loss">87%</strong>.
                </li>
                <li>
                  People who got suspicious <strong>before</strong> paying lost money 5% of the time. Those who only got
                  suspicious after paying: <strong className="about__loss">89%</strong>.
                </li>
                <li>The shape of the script says more about how a story ends than the scam’s name does.</li>
              </ul>
            </section>

            <section>
              <h3 className="about__h">Read with care</h3>
              <p className="small">
                These are associations in self-reported Reddit stories, not causes. 43% of stories don’t say how they
                ended, so every loss rate counts only known outcomes. Small scam types (pig butchering, romance) are too
                thin to quote on their own.
              </p>
            </section>

            <div className="about__footer">
              <a className="about__link" href={REPO_URL} target="_blank" rel="noreferrer">
                [ view the code on GitHub ↗ ]
              </a>
              <Dialog.Close asChild>
                <Button variant="default">[ back to the map ]</Button>
              </Dialog.Close>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
