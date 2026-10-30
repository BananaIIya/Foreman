import unittest
import jsonschema
from .live_review import SCHEMA, REVIEW_THRESHOLD, feedback
from .test_live_review import review


class SourceReviewTests(unittest.TestCase):
    def test_explicit_validation_basis_is_required(self):
        report=review()['review'];jsonschema.validate(report,SCHEMA)
        for basis in SCHEMA['properties']['validation_basis']['enum']:
            report['validation_basis']=basis;jsonschema.validate(report,SCHEMA)
        del report['validation_basis']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(report,SCHEMA)

    def test_unknown_basis_or_empty_concern_cannot_manufacture_steering(self):
        report=review()['review'];report['validation_basis']='grader_passed'
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(report,SCHEMA)
        report=review(action='continue')['review'];report['validation_basis']='not_yet_observed'
        self.assertIsNone(feedback(report))
        self.assertEqual(REVIEW_THRESHOLD,.65)


if __name__=='__main__':unittest.main()
