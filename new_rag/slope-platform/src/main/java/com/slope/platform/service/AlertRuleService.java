package com.slope.platform.service;

import com.slope.platform.common.BusinessException;
import com.slope.platform.dto.AlertRuleRequest;
import com.slope.platform.entity.AlertRule;
import com.slope.platform.repository.AlertRuleRepository;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 告警规则业务——本服务里最关键的一组写操作。
 *
 * <p>阈值决定现场会不会报警，因此这里多做了两件小事，都不是"为了好看"：
 * <ol>
 *   <li><b>等级白名单</b>：level 必须是 blue/yellow/orange/red。
 *       写成 "oragne" 这种拼写错误不会报错，但会让前端拿不到颜色、
 *       让按等级排序的处置优先级错乱——这种问题往往要到真出事时才发现。</li>
 *   <li><b>updated_at 自动刷新</b>（实体里的 @PreUpdate）：
 *       事后必须能回答"这条阈值什么时候被改过"。修改留痕是安全系统的底线。</li>
 * </ol>
 */
@Service
@Transactional(readOnly = true)
public class AlertRuleService {

    /** 与 Python 侧 alert_engine.LEVELS 保持一致，两端不能各定义一套。 */
    private static final Set<String> LEVELS = Set.of("blue", "yellow", "orange", "red");

    private final AlertRuleRepository alertRuleRepository;

    public AlertRuleService(AlertRuleRepository alertRuleRepository) {
        this.alertRuleRepository = alertRuleRepository;
    }

    public List<AlertRule> list(String metric, Boolean enabled) {
        return alertRuleRepository.search(
                (metric == null || metric.isBlank()) ? null : metric.trim(), enabled);
    }

    public AlertRule get(Integer id) {
        return alertRuleRepository.findById(id)
                .orElseThrow(() -> BusinessException.notFound("告警规则不存在: id=" + id));
    }

    @Transactional
    public AlertRule create(AlertRuleRequest request) {
        if (alertRuleRepository.existsByCode(request.code())) {
            throw BusinessException.conflict("规则编码已存在: " + request.code());
        }
        AlertRule rule = new AlertRule();
        apply(rule, request);
        return alertRuleRepository.save(rule);
    }

    @Transactional
    public AlertRule update(Integer id, AlertRuleRequest request) {
        AlertRule rule = get(id);
        if (alertRuleRepository.existsByCodeAndIdNot(request.code(), id)) {
            throw BusinessException.conflict("规则编码已被其它规则占用: " + request.code());
        }
        apply(rule, request);
        return alertRuleRepository.save(rule);
    }

    /**
     * 只改启停开关。
     *
     * <p>单独开一个接口，是因为"临时停用"是高频操作（传感器检修、边坡施工期）。
     * 如果只能走全量 PUT，前端必须先把整条规则读出来再原样回传，
     * 中间的任何一个字段误差都会连带改动阈值——那是安全上不能接受的副作用。
     */
    @Transactional
    public AlertRule setEnabled(Integer id, boolean enabled) {
        AlertRule rule = get(id);
        rule.setEnabled(enabled);
        return alertRuleRepository.save(rule);
    }

    /**
     * 删除规则。
     *
     * <p>注意：不会删除历史告警事件。{@code alert_event.rule_code} 是弱引用（没有外键），
     * 事件里还保存着当时触发的 threshold 快照，所以规则删掉后历史依然可读、可倒查。
     * 这是刻意的——删规则是为了不再产生新告警，不是为了抹掉过去。
     */
    @Transactional
    public void delete(Integer id) {
        alertRuleRepository.delete(get(id));
    }

    private void apply(AlertRule rule, AlertRuleRequest request) {
        String level = request.level().trim().toLowerCase();
        if (!LEVELS.contains(level)) {
            throw BusinessException.badRequest(
                    "不支持的告警等级: %s，可选值 %s".formatted(request.level(), LEVELS));
        }
        rule.setCode(request.code().trim());
        rule.setName(request.name().trim());
        rule.setMetric(request.metric().trim());
        rule.setThreshold(request.threshold());
        rule.setLevel(level);
        rule.setWindowHours(request.windowHours());
        rule.setDedupMinutes(request.dedupMinutes());
        rule.setDescription(request.description());
        if (request.enabled() != null) {
            rule.setEnabled(request.enabled());
        }
        // 显式赋值，保证 PUT 之后审计时间一定变化（即使 @PreUpdate 因为脏检查未触发）
        rule.setUpdatedAt(LocalDateTime.now());
    }
}
