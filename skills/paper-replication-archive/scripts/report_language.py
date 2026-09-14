"""Small bilingual report renderer; research content stays in its supplied language."""
from __future__ import annotations


LABELS = {
    'actual_work': ('实际完成的工作', 'Work completed'),
    'most_important_conclusion': ('科学结论', 'Scientific conclusion'),
    'targets': ('逐目标结果', 'Results by target'),
    'critic_failures': ('审查发现与修复', 'Review findings and repairs'),
    'acceptance_files': ('结果与验收文件', 'Results and acceptance files'),
    'decision_panels': ('需要用户决定的事项', 'Decisions needed'),
    'paper': ('论文信息', 'Paper'),
    'reading_summary': ('精读摘要', 'Reading summary'),
    'claims': ('证据支持的结论', 'Evidence-supported claims'),
    'negative_results': ('负面结果', 'Negative results'),
    'blockers': ('未解决的问题', 'Unresolved issues'),
    'integrity_files': ('证据文件', 'Evidence files'),
    'run_state': ('计算状态', 'Execution status'),
    'presentation_status': ('展示质量', 'Presentation quality'),
    'claim_status': ('科学验收', 'Scientific acceptance'),
    'comparison_verdict': ('比较结论', 'Comparison verdict'),
    'target_kind': ('目标类别', 'Target kind'),
    'allowed_wording': ('允许的结论表述', 'Allowed claim'),
}


def label(key, language):
    return LABELS.get(key, (str(key).replace('_', ' '),) * 2)[language == 'en']


def structured_report(title, document, language='zh-CN', *, state=None):
    lines = ['# ' + title, '',
             '> Archive completeness, execution success and scientific acceptance are separate.' if language == 'en'
             else '> 档案完整、计算完成与科学验收通过分别报告。', '']
    def bullets(value, depth=0):
        prefix = '  ' * depth
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    lines.append(prefix + '- ' + label(key, language) + ':')
                    bullets(item, depth + 1)
                else:
                    lines.append(prefix + '- ' + label(key, language) + ': ' + str(item))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    lines.append(prefix + '- ' + ('Record' if language == 'en' else '记录'))
                    bullets(item, depth + 1)
                else:
                    lines.append(prefix + '- ' + str(item))
        else:
            lines.append(prefix + '- ' + str(value))
        if value in ([], {}):
            lines.append(prefix + '- ' + ('None recorded' if language == 'en' else '无记录'))
    order = ['most_important_conclusion', 'reading_summary', 'targets', 'claims', 'negative_results',
             'blockers', 'acceptance_files', 'paper', 'actual_work', 'critic_failures', 'integrity_files', 'decision_panels']
    for key in order + sorted(set(document) - set(order) - {'schema_version', 'language', 'title'}):
        if key not in document or (key == 'decision_panels' and not document[key]):
            continue
        lines += ['## ' + label(key, language), '']
        value = document[key]
        if key == 'targets' and state:
            value = {target_id: dict(details) for target_id, details in value.items()}
            for target_id, details in value.items():
                current = state['targets'].get(target_id, {})
                for field in ('run_state', 'claim_status', 'comparison_verdict', 'presentation_status', 'target_kind'):
                    if field in current:
                        details[field] = current[field]
        bullets(value)
        lines.append('')
    return '\n'.join(lines)
