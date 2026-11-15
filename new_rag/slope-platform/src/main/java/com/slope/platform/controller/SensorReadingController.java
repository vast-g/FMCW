package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.entity.SensorReading;
import com.slope.platform.service.SensorReadingService;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 读数查询接口（只读）。
 *
 * <p>这不是需求里点名的接口，是为了让前端的趋势图有数据可取。
 * 之所以放在 Java 而不是让前端直接查库：走同一个鉴权入口，
 * 并且能在服务端限制查询窗口，避免有人写个循环把时序表拖垮。
 *
 * <p>注意这里只有 GET，<b>没有任何写读数的接口</b>——读数的写入方是采集链路，
 * 不是业务后端（见 README「与 Python 侧的边界」）。
 */
@RestController
@RequestMapping("/api/readings")
public class SensorReadingController {

    private final SensorReadingService sensorReadingService;

    public SensorReadingController(SensorReadingService sensorReadingService) {
        this.sensorReadingService = sensorReadingService;
    }

    @GetMapping
    public ApiResponse<List<SensorReading>> recent(
            @RequestParam(name = "site_id") Integer siteId,
            @RequestParam(name = "metric", required = false) String metric,
            @RequestParam(name = "hours", defaultValue = "24") int hours,
            @RequestParam(name = "limit", defaultValue = "500") int limit) {
        return ApiResponse.ok(sensorReadingService.recent(siteId, metric, hours, limit));
    }

    /**
     * 最新读数（每个「点位 × 指标」一条），供首页实时卡片使用。
     *
     * <p>和 {@link #recent} 的区别：recent 返回的是**窗口内的序列**（画趋势图用），
     * 这个返回的是**当前值**（显示"现在多少"用）。
     * 首页要 20 个当前值，如果拿 recent 去凑就得发 20 次请求，所以单开一个接口。
     *
     * <p>{@code site_id} 不传则返回全部点位 —— 首页正是要一次拿全，
     * 所以这里刻意不做成必填（与 recent 不同，那边是按点位画图的，缺了没意义）。
     */
    @GetMapping("/latest")
    public ApiResponse<List<SensorReading>> latest(
            @RequestParam(name = "site_id", required = false) Integer siteId) {
        return ApiResponse.ok(sensorReadingService.latest(siteId));
    }
}
