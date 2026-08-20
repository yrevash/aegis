'use client'

import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

import { parseAnswer, type Block, type Inline } from './answerMarkdown'

/** The emphasis runs of one line, as elements. */
function Spans({ spans }: { spans: Inline[] }): ReactElement {
  return (
    <>
      {spans.map((span, index) => {
        if (span.kind === 'bold') {
          return (
            <strong key={index} className="font-semibold text-foreground">
              {span.text}
            </strong>
          )
        }
        if (span.kind === 'italic') {
          return (
            <em key={index} className="italic">
              {span.text}
            </em>
          )
        }
        if (span.kind === 'code') {
          return (
            <code
              key={index}
              className="rounded border border-border bg-surface-2 px-1 py-0.5 font-mono text-[0.82em]"
            >
              {span.text}
            </code>
          )
        }
        return <span key={index}>{span.text}</span>
      })}
    </>
  )
}

/** One block, in the type ramp DESIGN.md §3 sets for body copy. */
function BlockView({ block }: { block: Block }): ReactNode {
  switch (block.kind) {
    case 'heading': {
      const size =
        block.level === 1
          ? 'text-[0.95rem] font-semibold'
          : block.level === 2
            ? 'text-[0.875rem] font-semibold'
            : 'text-[0.82rem] font-semibold'
      return (
        <p className={cn('mt-1 text-foreground first:mt-0', size)}>
          <Spans spans={block.spans} />
        </p>
      )
    }
    case 'list':
      return (
        <ol
          start={block.ordered ? block.start : undefined}
          className={cn(
            'flex flex-col gap-1 pl-1 text-sm leading-relaxed text-foreground',
            block.ordered ? 'list-decimal' : 'list-disc',
            'ml-4',
          )}
        >
          {block.items.map((item, index) => (
            <li key={index} className="pl-1 marker:text-muted-foreground">
              <Spans spans={item} />
            </li>
          ))}
        </ol>
      )
    case 'quote':
      return (
        <p className="border-l-2 border-blue-200 pl-3 text-sm leading-relaxed text-muted-foreground">
          <Spans spans={block.spans} />
        </p>
      )
    case 'code':
      return (
        <pre className="overflow-x-auto rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[0.76rem] leading-relaxed text-foreground">
          {block.text}
        </pre>
      )
    case 'rule':
      return <hr className="border-border" />
    case 'paragraph':
      return (
        <p className="text-sm leading-relaxed break-words text-foreground">
          <Spans spans={block.spans} />
        </p>
      )
  }
}

/**
 * The answer, rendered as the structured document the model wrote.
 *
 * `whitespace-pre-wrap` was the right call while the answer was a paragraph and the
 * wrong one the moment it became a briefing: a model that writes `## What to check` and
 * a numbered list had that rendered as hashes, asterisks and digits running down the
 * page. This turns {@link parseAnswer}'s typed tree into elements — never HTML, never
 * `dangerouslySetInnerHTML`, and with no link ever synthesised from model output.
 *
 * The caret is passed in rather than appended here, because it belongs at the end of the
 * **last** block and only the caller knows whether more is still coming.
 */
export function AnswerBody({ text, caret }: { text: string; caret?: ReactNode }): ReactElement {
  const blocks = parseAnswer(text)
  return (
    <div className="flex flex-col gap-2.5">
      {blocks.map((block, index) => (
        <div key={index} className="min-w-0">
          <BlockView block={block} />
          {caret !== undefined && index === blocks.length - 1 && caret}
        </div>
      ))}
    </div>
  )
}
