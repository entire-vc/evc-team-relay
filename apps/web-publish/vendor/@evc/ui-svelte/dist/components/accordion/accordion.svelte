<script lang="ts">
  import { Accordion as AccordionPrimitive } from 'bits-ui';

  type Props = AccordionPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<AccordionPrimitive.Root {...restProps}>
  {@render children?.()}
</AccordionPrimitive.Root>
