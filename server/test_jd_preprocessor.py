import unittest

from services.jd_preprocessor import clean_web_jd


class JDPreprocessorTests(unittest.TestCase):
    def test_clean_web_jd_truncates_recommendation_tail(self):
        raw = (
            "python工程师（AI方向） 1.2-1.8万 天智云算(深圳)科技有限公司 深圳 3-5年 大专 "
            "职位描述 岗位职责 负责大模型应用开发 任职要求 熟练使用Python "
            "职位福利 天智云算(深圳)科技有限公司 民营·20-99人·产业互联网平台 "
            "认证资质 营业执照信息 为您推荐更多相似职位 python开发工程师 1.1-1.7万 软通动力 深圳 "
            "周边城市 最新招聘 热门城市 热门职位 热门公司 立即申请 收藏职位 举报职位 取消"
        )

        cleaned = clean_web_jd(raw)

        self.assertIn("职位描述", cleaned)
        self.assertIn("任职要求", cleaned)
        self.assertIn("职位福利", cleaned)
        self.assertNotIn("认证资质", cleaned)
        self.assertNotIn("为您推荐更多相似职位", cleaned)
        self.assertNotIn("热门城市", cleaned)
        self.assertNotIn("立即申请", cleaned)

    def test_clean_web_jd_keeps_main_facts_before_cutoff(self):
        raw = (
            "数据分析师 1.1-1.5万 软通动力信息技术(集团)股份有限公司 深圳 3-5年 本科 招1人 "
            "工作地址 深圳龙岗区天安云谷 职位描述 岗位职责 负责数据分析 岗位要求 熟练使用Excel、SQL "
            "营业执照信息 热门职位 热门公司"
        )

        cleaned = clean_web_jd(raw)

        self.assertIn("数据分析师", cleaned)
        self.assertIn("1.1-1.5万", cleaned)
        self.assertIn("软通动力信息技术(集团)股份有限公司", cleaned)
        self.assertIn("工作地址", cleaned)
        self.assertIn("岗位要求", cleaned)
        self.assertNotIn("热门职位", cleaned)


if __name__ == "__main__":
    unittest.main()
