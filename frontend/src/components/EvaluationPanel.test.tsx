import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EvaluationPanel } from './EvaluationPanel'

const originalFetch = globalThis.fetch

const evaluationV1 = {
  version: 1,
  round: 1,
  createdAt: '2026-04-02T00:00:00+00:00',
  result: {
    builtin: {
      relevance: { score: 4.5, label: 'Relevance', reason: 'good' },
      coherence: { score: 4.2, label: 'Coherence', reason: 'clear' },
      fluency: { score: 4.1, label: 'Fluency', reason: 'smooth' },
      task_adherence: { score: 4.0, label: 'Task Adherence', reason: 'hidden' },
    },
    plan_quality: {
      overall: 4.1,
      summary: 'Plan summary v1',
      focus_areas: ['KPI Evidence Readiness'],
      metrics: {
        relevance: { score: 4.5, label: 'Relevance', reason: 'good' },
        coherence: { score: 4.2, label: 'Coherence', reason: 'clear' },
        fluency: { score: 4.1, label: 'Fluency', reason: 'smooth' },
        appeal: { score: 4.0, label: 'Customer Appeal', reason: 'solid' },
        differentiation: { score: 3.9, label: 'Differentiation', reason: 'needs more contrast' },
        kpi_validity: { score: 4.0, label: 'KPI Validity', reason: 'reasonable' },
        brand_tone: { score: 4.3, label: 'Brand Consistency', reason: 'steady tone' },
        plan_structure_readiness: { score: 4.4, label: 'Plan Structure Readiness', details: { title: true, kpi: true } },
        target_fit_readiness: { score: 4.0, label: 'Target Fit Readiness' },
        kpi_evidence_readiness: { score: 3.4, label: 'KPI Evidence Readiness', details: { assumptions: true, baseline: false } },
        offer_specificity: { score: 4.1, label: 'Offer Specificity' },
        travel_law_compliance: { score: 4.8, label: 'Travel Law', details: { disclaimer: true, fee_display: true } },
      },
    },
    asset_quality: {
      overall: 3.4,
      summary: 'Asset summary v1',
      focus_areas: ['CTA Visibility'],
      metrics: {
        cta_visibility: { score: 3.0, label: 'CTA Visibility', details: { cta_button: false, contact: true } },
        value_visibility: { score: 3.2, label: 'Value Visibility' },
        trust_signal_presence: { score: 3.7, label: 'Trust Signal Presence' },
        disclosure_completeness: { score: 3.4, label: 'Disclosure Completeness' },
        accessibility_readiness: { score: 3.5, label: 'Accessibility Readiness' },
      },
    },
    regression_guard: {
      summary: 'No significant regression was detected.',
      has_regressions: false,
      degraded_metrics: [],
      improved_metrics: [],
      plan_overall_delta: 0,
      asset_overall_delta: 0,
    },
    legacy_overall: 3.75,
  },
}

const evaluationV2 = {
  version: 2,
  round: 2,
  createdAt: '2026-04-02T02:00:00+00:00',
  result: {
    builtin: {
      relevance: { score: 4.8, label: 'Relevance', reason: 'great' },
      coherence: { score: 4.6, label: 'Coherence', reason: 'strong flow' },
      fluency: { score: 4.5, label: 'Fluency', reason: 'sharp copy' },
      task_adherence: { score: 4.4, label: 'Task Adherence', reason: 'hidden' },
    },
    plan_quality: {
      overall: 4.6,
      summary: 'Plan summary v2',
      focus_areas: ['Differentiation'],
      metrics: {
        relevance: { score: 4.8, label: 'Relevance', reason: 'great' },
        coherence: { score: 4.6, label: 'Coherence', reason: 'strong flow' },
        fluency: { score: 4.5, label: 'Fluency', reason: 'sharp copy' },
        appeal: { score: 4.7, label: 'Customer Appeal', reason: 'more compelling' },
        differentiation: { score: 4.2, label: 'Differentiation', reason: 'clearer' },
        kpi_validity: { score: 4.4, label: 'KPI Validity', reason: 'better grounded' },
        brand_tone: { score: 4.5, label: 'Brand Consistency', reason: 'consistent' },
        plan_structure_readiness: { score: 4.7, label: 'Plan Structure Readiness', details: { title: true, kpi: true } },
        target_fit_readiness: { score: 4.5, label: 'Target Fit Readiness' },
        kpi_evidence_readiness: { score: 4.1, label: 'KPI Evidence Readiness', details: { assumptions: true, baseline: true } },
        offer_specificity: { score: 4.4, label: 'Offer Specificity' },
        travel_law_compliance: { score: 5.0, label: 'Travel Law', details: { disclaimer: true, fee_display: true } },
      },
    },
    asset_quality: {
      overall: 4.2,
      summary: 'Asset summary v2',
      focus_areas: ['Disclosure Completeness'],
      metrics: {
        cta_visibility: { score: 4.4, label: 'CTA Visibility', details: { cta_button: true, contact: true } },
        value_visibility: { score: 4.1, label: 'Value Visibility' },
        trust_signal_presence: { score: 4.0, label: 'Trust Signal Presence' },
        disclosure_completeness: { score: 3.9, label: 'Disclosure Completeness' },
        accessibility_readiness: { score: 4.5, label: 'Accessibility Readiness' },
      },
    },
    regression_guard: {
      summary: 'Improved 4 key metrics compared with the prior version.',
      has_regressions: false,
      degraded_metrics: [],
      improved_metrics: [
        { key: 'relevance', label: 'Relevance', area: 'plan', current: 4.8, previous: 4.5, delta: 0.3 },
        { key: 'cta_visibility', label: 'CTA Visibility', area: 'asset', current: 4.4, previous: 3.0, delta: 1.4 },
      ],
      plan_overall_delta: 0.5,
      asset_overall_delta: 0.8,
    },
    legacy_overall: 4.4,
  },
}

const legacyEvaluationV1 = {
  version: 1,
  round: 1,
  createdAt: '2026-04-02T00:00:00+00:00',
  result: {
    builtin: {
      relevance: { score: 4, reason: 'good' },
      coherence: { score: 4, reason: 'clear' },
      fluency: { score: 4, reason: 'smooth' },
      task_adherence: { score: 1, reason: 'hidden' },
    },
    marketing_quality: {
      overall: 4,
      appeal: 4,
      differentiation: 3.5,
      kpi_validity: 4,
      brand_tone: 4,
      reason: 'solid',
    },
    custom: {
      plan_structure_readiness: { score: 0.8, details: { title: true, kpi: true } },
      senior_fit_readiness: { score: 0.8 },
      kpi_evidence_readiness: { score: 0.6, details: { assumptions: true, baseline: false } },
      offer_specificity: { score: 0.8 },
      travel_law_compliance: { score: 1, details: { disclaimer: true, fee_display: false } },
      cta_visibility: { score: 0.25, details: { cta_button: false, contact: true } },
      value_visibility: { score: 0.5 },
      trust_signal_presence: { score: 0.5 },
      disclosure_completeness: { score: 0.5 },
      accessibility_readiness: { score: 0.5 },
    },
  },
}

const legacyEvaluationV2 = {
  version: 2,
  round: 2,
  createdAt: '2026-04-02T02:00:00+00:00',
  result: {
    builtin: {
      relevance: { score: 5, reason: 'great' },
      coherence: { score: 4.5, reason: 'better' },
      fluency: { score: 4.5, reason: 'sharper' },
      task_adherence: { score: 1, reason: 'hidden' },
    },
    marketing_quality: {
      overall: 5,
      appeal: 5,
      differentiation: 4.5,
      kpi_validity: 4.5,
      brand_tone: 5,
      reason: 'excellent',
    },
    custom: {
      plan_structure_readiness: { score: 1, details: { title: true, kpi: true } },
      senior_fit_readiness: { score: 0.9 },
      kpi_evidence_readiness: { score: 0.8, details: { assumptions: true, baseline: true } },
      offer_specificity: { score: 0.9 },
      travel_law_compliance: { score: 1, details: { disclaimer: true, fee_display: true } },
      cta_visibility: { score: 0.75, details: { cta_button: true, contact: true } },
      value_visibility: { score: 0.8 },
      trust_signal_presence: { score: 0.8 },
      disclosure_completeness: { score: 0.9 },
      accessibility_readiness: { score: 0.9 },
    },
  },
}

function makeSnapshot(evaluations = [evaluationV1]) {
  return {
    textContents: [],
    images: [],
    toolEvents: [],
    metrics: null,
    evaluations,
  }
}

function createJsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('EvaluationPanel', () => {
  const mockFetch = vi.fn()
  const t = (key: string) => ({
    'eval.title': 'Evaluation',
    'eval.run': 'Run Evaluation',
    'eval.running': 'Running...',
    'eval.compare': 'Improvement Round Comparison',
    'eval.round': 'Evaluation #{n}',
    'eval.compare.preview_hint': 'Comparison changes only inside this panel.',
    'eval.compare.selection': 'Comparing {current} against {target}',
    'eval.compare.current': 'Current version',
    'eval.compare.target': 'Compared version',
    'eval.compare.switch_target': 'Switch comparison target',
    'eval.compare.improved': 'Improved',
    'eval.compare.degraded': 'Regressed',
    'eval.compare.unchanged': 'Unchanged',
    'eval.compare.detail_changes': 'Checks that changed state',
    'eval.builtin': 'AI Quality Metrics',
    'eval.plan_quality': 'Plan Quality',
    'eval.asset_quality': 'Asset Quality',
    'eval.regression_guard': 'Regression Guard',
    'eval.focus_areas': 'Priority Focus Areas',
    'eval.ai_review': 'AI Review',
    'eval.marketing_review': 'Marketing Review',
    'eval.execution_readiness': 'Plan Execution Readiness',
    'eval.asset_readiness': 'Asset Delivery Readiness',
    'eval.regression.none': 'No significant regression was detected.',
    'eval.no_result': 'No evaluation results yet.',
    'eval.refine': 'Refine from results',
    'eval.refine.latest_only': 'Refine only on the latest version.',
    'eval.portal': 'View in Foundry Portal',
    'eval.relevance': 'Relevance',
    'eval.coherence': 'Coherence',
    'eval.fluency': 'Fluency',
    'eval.task_adherence': 'Task Adherence',
    'eval.appeal': 'Customer Appeal',
    'eval.differentiation': 'Differentiation',
    'eval.kpi_validity': 'KPI Validity',
    'eval.brand_tone': 'Brand Consistency',
    'eval.plan_structure_readiness': 'Plan Structure Readiness',
    'eval.target_fit_readiness': 'Target Fit Readiness',
    'eval.senior_fit_readiness': 'Target Fit Readiness',
    'eval.kpi_evidence_readiness': 'KPI Evidence Readiness',
    'eval.offer_specificity': 'Offer Specificity',
    'eval.travel_law_compliance': 'Travel Law',
    'eval.cta_visibility': 'CTA Visibility',
    'eval.value_visibility': 'Value Visibility',
    'eval.trust_signal_presence': 'Trust Signal Presence',
    'eval.disclosure_completeness': 'Disclosure Completeness',
    'eval.accessibility_readiness': 'Accessibility Readiness',
  }[key] ?? key)

  beforeEach(() => {
    globalThis.fetch = mockFetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('keeps evaluation history separated by artifact version', () => {
    const { rerender } = render(
      <EvaluationPanel
        query="q"
        response="plan A"
        html="<p>A</p>"
        artifactVersion={1}
        evaluations={[evaluationV1]}
        versions={[makeSnapshot([evaluationV1])]}
        t={t}
      />,
    )

    expect(screen.getAllByText('Plan summary v1').length).toBeGreaterThan(0)

    rerender(
      <EvaluationPanel
        query="q"
        response="plan B"
        html="<p>B</p>"
        artifactVersion={2}
        evaluations={[]}
        versions={[makeSnapshot([evaluationV1]), makeSnapshot([])]}
        t={t}
      />,
    )

    expect(screen.queryByText('Plan summary v1')).toBeNull()
    expect(screen.getByText('No evaluation results yet.')).toBeInTheDocument()

    rerender(
      <EvaluationPanel
        query="q"
        response="plan A"
        html="<p>A</p>"
        artifactVersion={1}
        evaluations={[evaluationV1]}
        versions={[makeSnapshot([evaluationV1]), makeSnapshot([])]}
        t={t}
      />,
    )

    expect(screen.getAllByText('Plan summary v1').length).toBeGreaterThan(0)
  })

  it('keeps evaluation visible when brochure html arrives later', () => {
    const { rerender } = render(
      <EvaluationPanel
        query="q"
        response="plan A"
        html=""
        artifactVersion={1}
        evaluations={[evaluationV1]}
        versions={[makeSnapshot([evaluationV1])]}
        t={t}
      />,
    )

    expect(screen.getByText('AI Quality Metrics')).toBeInTheDocument()
    expect(screen.getAllByText('Asset summary v1').length).toBeGreaterThan(0)
    expect(screen.getByText('Plan Quality: KPI Evidence Readiness')).toBeInTheDocument()

    rerender(
      <EvaluationPanel
        query="q"
        response="plan A"
        html="<p>brochure ready</p>"
        artifactVersion={1}
        evaluations={[evaluationV1]}
        versions={[makeSnapshot([evaluationV1])]}
        t={t}
      />,
    )

    expect(screen.getAllByText('Asset summary v1').length).toBeGreaterThan(0)
    expect(screen.getByText('Asset Quality: CTA Visibility')).toBeInTheDocument()
  })

  it('saves new evaluations through the version callback', async () => {
    mockFetch.mockResolvedValueOnce(createJsonResponse({
      plan_quality: evaluationV2.result.plan_quality,
      asset_quality: evaluationV2.result.asset_quality,
      regression_guard: evaluationV2.result.regression_guard,
      legacy_overall: evaluationV2.result.legacy_overall,
      evaluation_meta: { version: 2, round: 2, created_at: '2026-04-02T01:00:00+00:00' },
    }))

    const onEvaluationRecorded = vi.fn()

    render(
      <EvaluationPanel
        query="q"
        response="plan B"
        html="<p>B</p>"
        conversationId="conv-1"
        artifactVersion={2}
        evaluations={[]}
        versions={[makeSnapshot([evaluationV1]), makeSnapshot([])]}
        onEvaluationRecorded={onEvaluationRecorded}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Evaluation' }))

    await waitFor(() => {
      expect(onEvaluationRecorded).toHaveBeenCalledWith(expect.objectContaining({
        version: 2,
        round: 2,
        result: expect.objectContaining({
          plan_quality: expect.objectContaining({ overall: 4.6 }),
          asset_quality: expect.objectContaining({ overall: 4.2 }),
        }),
      }))
    })
  })

  it('derives grouped comparison from legacy evaluations and restores builtin metrics', () => {
    render(
      <EvaluationPanel
        query="q"
        response="plan B"
        html="<p>B</p>"
        artifactVersion={2}
        evaluations={[legacyEvaluationV2]}
        versions={[makeSnapshot([legacyEvaluationV1]), makeSnapshot([legacyEvaluationV2])]}
        t={t}
      />,
    )
          expect(screen.getByText('AI Quality Metrics')).toBeInTheDocument()

    expect(screen.getByText('Comparing v2 against v1')).toBeInTheDocument()
    expect(screen.getByText('Current version')).toBeInTheDocument()
    expect(screen.getByText('Compared version')).toBeInTheDocument()
    expect(screen.getAllByText(/Improved/).length).toBeGreaterThan(0)
          expect(screen.getAllByText('ターゲット適合性').length).toBeGreaterThan(0)
    expect(screen.getByText('Checks that changed state')).toBeInTheDocument()
    expect(screen.getAllByText('Relevance').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/fee_display/).length).toBeGreaterThan(0)
    expect(screen.queryByText('Task Adherence')).toBeNull()
  })
})
