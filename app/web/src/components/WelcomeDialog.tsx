import { Dialog } from 'radix-ui'
import { Button } from '@/components/ui/button'

interface Props {
  open: boolean
  /** Close and go to the map. */
  onExplore: () => void
  /** Close and open the "how we built it" window instead. */
  onHowWeBuiltIt: () => void
}

/** First-visit welcome: a quick summary, then straight to the map. */
export function WelcomeDialog({ open, onExplore, onHowWeBuiltIt }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onExplore()}>
      <Dialog.Portal>
        <Dialog.Overlay className="about-overlay" />
        <Dialog.Content className="window about welcome">
          <div className="window__bar">
            <Dialog.Close className="window__close about__close" aria-label="Close" />
            <Dialog.Title className="window__title">welcome.txt</Dialog.Title>
          </div>
          <div className="about__body">
            <div>
              <p className="welcome__kicker">scam scripts</p>
              <h2 className="welcome__title">Scams follow scripts. We mapped them.</h2>
            </div>
            <Dialog.Description className="about__lede">
              We turned <strong>2,074 real victim stories</strong> from Reddit and CFPB complaints into step-by-step timelines and mapped how each
              type of scam plays out: where people get hooked, and where they get out.
            </Dialog.Description>
            <ul className="welcome__list">
              <li>
                <span className="welcome__num">01</span>
                <span>
                  <strong>Explore the map.</strong> Each box is a step in a scam. Click one to read the real stories behind it.
                </span>
              </li>
              <li>
                <span className="welcome__num">02</span>
                <span>
                  <strong>Spot the turning points.</strong> Once someone makes even a small payment, the loss rate jumps from
                  60% to <strong className="about__loss">96%</strong>.
                </span>
              </li>
              <li>
                <span className="welcome__num">03</span>
                <span>
                  <strong>Check a message.</strong> Paste anything suspicious and see which scam it looks like, and what usually
                  comes next.
                </span>
              </li>
            </ul>
            <p className="muted small">Built by Faraj and Arya. Associations in self-reported stories, not causes.</p>
            <div className="about__footer">
              <Button variant="text" size="bare" onClick={onHowWeBuiltIt}>
                [ how we built it ]
              </Button>
              <Button variant="default" onClick={onExplore} autoFocus>
                [ explore the map → ]
              </Button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
