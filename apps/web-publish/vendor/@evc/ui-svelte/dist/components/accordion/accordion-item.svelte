<script lang="ts">
  import { Accordion as AccordionPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AccordionPrimitive.ItemProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AccordionPrimitive.Item class={cn('border-b', className)} {...restProps}>
  {@render children?.()}
</AccordionPrimitive.Item>
